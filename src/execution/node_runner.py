"""
Hyperliquid Live / Testnet Quantitative TradingNode Runner.
Configures and launches NautilusTrader TradingNode connected to Hyperliquid Testnet
(https://api.hyperliquid-testnet.xyz), streams live market data,
attaches 4 modular strategy actors under the unified PortfolioGuard:
  1. TrendContinuationSMC
  2. HourlyFundingFade
  3. OrderBookImbalance
  4. VwapOiMomentum
Uses eth_account.Account.create() to dynamically provision an ephemeral burner agent wallet
if no private key is present in .env.
Renders an interactive Rich terminal surveillance dashboard tracking live market state,
active zones, real-time funding rates, and virtual portfolio margin/PnL.
Strictly zero pandas dependencies.
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import os
import json
from nautilus_trader.execution.messages import CancelOrder
from nautilus_trader.model.identifiers import ClientOrderId, StrategyId
import time
import signal
import argparse
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

from eth_account import Account
import polars as pl
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout

from nautilus_trader.config import TradingNodeConfig, PortfolioConfig, LoggingConfig
from nautilus_trader.common.config import OrderEmulatorConfig, InstrumentProviderConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.adapters.hyperliquid.config import (
    HyperliquidDataClientConfig,
    HyperliquidExecClientConfig,
    HyperliquidEnvironment,
)
from nautilus_trader.adapters.hyperliquid.factories import (
    HyperliquidLiveDataClientFactory,
    HyperliquidLiveExecClientFactory,
)
from nautilus_trader.model.identifiers import Venue, InstrumentId, Symbol

from src.strategies.continuation import TrendContinuationSMC, TrendContinuationConfig
from src.strategies.funding_fade import HourlyFundingFade, HourlyFundingFadeConfig
from src.strategies.orderbook_scalp import OrderBookImbalance, OrderBookImbalanceConfig
from src.strategies.vwap_momentum import VwapOiMomentum, VwapOiMomentumConfig
from src.risk.portfolio_guard import PortfolioGuard
from src.utils.mcp_client import HyperliquidInfoClient

console = Console()


def generate_or_load_wallet() -> Tuple[str, str, bool]:
    """
    Load private key from environment or generate an ephemeral burner wallet.
    Returns (address, private_key_hex, is_ephemeral).
    """
    env_key = os.getenv("HYPERLIQUID_TESTNET_KEY") or os.getenv("HYPERLIQUID_PRIVATE_KEY")
    if env_key:
        try:
            account = Account.from_key(env_key)
            return account.address, env_key, False
        except Exception as e:
            console.print(f"[yellow]Failed to load key from environment: {e}. Generating burner key.[/yellow]")

    # Provision ephemeral burner wallet
    burner = Account.create()
    return burner.address, burner.key.hex(), True


def create_surveillance_table(
    top_coins: List[str],
    continuation_strat: Optional[TrendContinuationSMC],
    funding_strat: Optional[HourlyFundingFade],
    scalp_strat: Optional[OrderBookImbalance],
    vwap_strat: Optional[VwapOiMomentum],
    guard: PortfolioGuard,
    info_client: HyperliquidInfoClient,
    node: Optional[TradingNode],
    wallet_address: str,
    is_ephemeral: bool,
    is_paper: bool = False,
) -> Table:
    try:
        meta, asset_ctxs = info_client.get_meta_and_asset_ctxs()
        ctx_map = {m.get("name"): ctx for m, ctx in zip(meta.get("universe", []), asset_ctxs)}
    except Exception:
        ctx_map = {}
    """Generate the Rich interactive dashboard for multi-strategy node state."""
    # Main market surveillance table
    table = Table(
        title="HYPERLIQUID QUANTITATIVE NODE SURVEILLANCE",
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Symbol", justify="left", style="bold white")
    table.add_column("Last Price", justify="right", style="cyan")
    table.add_column("5M Gain", justify="right")
    table.add_column("30M Gain", justify="right")
    table.add_column("24H Gain", justify="right")
    table.add_column("4H Trend", justify="center")
    table.add_column("30M Zones", justify="center")
    table.add_column("5M Structure", justify="left")
    table.add_column("Funding APR", justify="right")
    table.add_column("Active Position", justify="center")

    for coin in top_coins:
        instr_id_str = f"{coin}-USD-PERP.HYPERLIQUID"
        cont_state = continuation_strat.states.get(instr_id_str) if continuation_strat else None

# Price and Context
        ctx = ctx_map.get(coin, {})
        current_px = float(ctx.get("oraclePx", 0.0))
        prev_day_px = float(ctx.get("prevDayPx", 0.0))
        
        price_str = f"${current_px:,.4f}" if current_px else "Awaiting"
        
        # 24H Gain (resetting at midnight UTC per Hyperliquid prevDayPx)
        gain_24h = ((current_px - prev_day_px) / prev_day_px) * 100 if prev_day_px and current_px else 0.0
        g24_color = "green" if gain_24h >= 0 else "red"
        g24_sign = "+" if gain_24h >= 0 else ""
        g24_str = f"[{g24_color}]{g24_sign}{gain_24h:.2f}%[/{g24_color}]"

        # 5M and 30M Gain
        gain_5m_str = "[dim]-[/dim]"
        gain_30m_str = "[dim]-[/dim]"
        if cont_state and current_px:
            if cont_state.last_5m_bar:
                open_5m = cont_state.last_5m_bar.open.as_double()
                g5 = ((current_px - open_5m) / open_5m) * 100
                g5_color = "green" if g5 >= 0 else "red"
                gain_5m_str = f"[{g5_color}]{'+' if g5 >= 0 else ''}{g5:.2f}%[/{g5_color}]"
            
            if cont_state.recent_30m_bars:
                open_30m = cont_state.recent_30m_bars[-1].open.as_double()
                g30 = ((current_px - open_30m) / open_30m) * 100
                g30_color = "green" if g30 >= 0 else "red"
                gain_30m_str = f"[{g30_color}]{'+' if g30 >= 0 else ''}{g30:.2f}%[/{g30_color}]"

        # 4H Trend

        trend_str = "[dim]NEUTRAL[/dim]"
        if cont_state:
            if cont_state.trend_state == "BULLISH":
                trend_str = "[bold green]▲ BULL (50>200)[/bold green]"
            elif cont_state.trend_state == "BEARISH":
                trend_str = "[bold red]▼ BEAR (50<200)[/bold red]"
            else:
                trend_str = "[yellow]◆ NEUTRAL[/yellow]"

        # 30M Zones
        zones_str = "[dim]0D / 0S[/dim]"
        if cont_state:
            d_count = len([z for z in cont_state.demand_zones if not z.mitigated])
            s_count = len([z for z in cont_state.supply_zones if not z.mitigated])
            d_fmt = f"[green]{d_count}D[/green]" if d_count > 0 else "0D"
            s_fmt = f"[red]{s_count}S[/red]" if s_count > 0 else "0S"
            zones_str = f"{d_fmt} / {s_fmt}"

        # 5M Structure / MSS
        struct_str = "[dim]Scanning[/dim]"
        if cont_state:
            if cont_state.zone_in_play:
                z_type = cont_state.zone_in_play.zone_type
                if z_type == "DEMAND":
                    level = cont_state.recent_swing_high
                    level_s = f"{level:.2f}" if level else "N/A"
                    struct_str = f"[yellow]TEST DEMAND (Brk>{level_s})[/yellow]"
                else:
                    level = cont_state.recent_swing_low
                    level_s = f"{level:.2f}" if level else "N/A"
                    struct_str = f"[yellow]TEST SUPPLY (Brk<{level_s})[/yellow]"
            elif cont_state.recent_swing_high or cont_state.recent_swing_low:
                struct_str = "[cyan]Armed & Tracking[/cyan]"

        # Funding APR (from batched ctx_map)
        funding_rate = None
        if "funding" in ctx:
            funding_rate = float(ctx["funding"]) * 24 * 365
            
        if funding_rate is not None:
            funding_col = "bold red" if funding_rate > 0.50 else ("bold green" if funding_rate < -0.50 else "white")
            funding_str = f"[{funding_col}]{funding_rate:+.1%}[/{funding_col}]"
        else:
            funding_str = "[dim]--[/dim]"

        # Position tracking across strategies
        pos_str = "[dim]FLAT[/dim]"
        if node and node.portfolio:
            instr_id = InstrumentId(Symbol(f"{coin}-USD-PERP"), Venue("HYPERLIQUID"))
            if not node.portfolio.is_flat(instr_id):
                if node.portfolio.is_net_long(instr_id):
                    pos_str = "[bold green]LONG 🟢[/bold green]"
                elif node.portfolio.is_net_short(instr_id):
                    pos_str = "[bold red]SHORT 🔴[/bold red]"

        table.add_row(
            coin,
            price_str,
            gain_5m_str,
            gain_30m_str,
            g24_str,
            trend_str,
            zones_str,
            struct_str,
            funding_str,
            pos_str,
        )

    # Risk Engine Status Banner
    circuit_color = "bold green" if not guard.is_circuit_breaker_triggered else "bold red"
    circuit_text = "NORMAL" if not guard.is_circuit_breaker_triggered else "CIRCUIT BREAKER TRIPPED (HALTED)"
    open_pos = len(guard.active_instrument_directions)
    wallet_type = "EPHEMERAL BURNER" if is_ephemeral else "ENVIRONMENT KEY"

    endpoint = "api.hyperliquid.xyz (MAINNET DATA + LOCAL EMULATOR)" if is_paper else ("api.hyperliquid-testnet.xyz" if is_ephemeral else "api.hyperliquid-testnet.xyz")
    prospects_str = f"\nAI Prospects: {guard.prospect_biases}" if guard.prospect_biases else ""
    table.caption = (
        f"Node Wallet: {wallet_address[:8]}...{wallet_address[-6:]} ({wallet_type}) | "
        f"Endpoint: {endpoint}\n"
        f"Circuit Breaker: [{circuit_color}]{circuit_text}[/{circuit_color}] | "
        f"Active Positions: {open_pos}/4 | "
        f"Pending Stale Orders Tracked: {len(guard.pending_orders)}"
        f"{prospects_str}"
    )

    return table


class HyperliquidNodeRunner:
    """
    Production-grade Multi-Strategy Live TradingNode Runner for Hyperliquid Testnet.
    Coordinates:
      - Ephemeral burner wallet generation (eth_account)
      - Hyperliquid Testnet Data & Execution client adapters
      - 4 Modular Strategy Actors
      - Unified PortfolioGuard risk engine
      - Interactive Rich live dashboard
    """

    def __init__(
        self,
        top_n: int = 20,
        trader_id: str = "HL-NODE-001",
        risk_pct: float = 0.01,
        rr_ratio: float = 2.5,
        paper: bool = False,
    ):
        self.top_n = top_n
        self.trader_id = trader_id
        self.risk_pct = risk_pct
        self.rr_ratio = rr_ratio
        self.paper = paper

        self.info_client = HyperliquidInfoClient(network="mainnet" if paper else "testnet")
        self.wallet_address, self._private_key, self.is_ephemeral = generate_or_load_wallet()

        self.guard = PortfolioGuard(max_strategy_equity_pct=25.0, max_total_open_positions=10, max_daily_drawdown_pct=0.20)

        self.node: Optional[TradingNode] = None
        self.continuation_strat: Optional[TrendContinuationSMC] = None
        self.funding_strat: Optional[HourlyFundingFade] = None
        self.scalp_strat: Optional[OrderBookImbalance] = None
        self.vwap_strat: Optional[VwapOiMomentum] = None

        self.top_coins: List[str] = []
        self._is_running = False
        self.prospects_path = os.path.join(os.getcwd(), "bridge", "prospects.json")
        self.prospect_biases: Dict[str, str] = {}

        # Paper trading state persistence
        self.paper_state_path = os.path.join(os.getcwd(), "bridge", "paper_state.json")
        self._paper_starting_equity: float = 100.0  # overridden by saved state on startup
        self._paper_realized_pnl: float = 0.0

    def load_paper_state(self) -> Dict[str, Any]:
        """
        Load persisted paper trading equity from bridge/paper_state.json so that
        restarts do NOT reset the paper balance back to $100.
        Returns the saved state dict or {} if no state exists yet.
        """
        if not self.paper:
            return {}
        try:
            if os.path.exists(self.paper_state_path):
                with open(self.paper_state_path, "r") as f:
                    state = json.load(f)
                equity = float(state.get("equity", 100.0))
                realized_pnl = float(state.get("realized_pnl", 0.0))
                session = state.get("session_start", "Unknown")
                console.print(
                    f"[bold cyan]📂 Restored paper state: Equity=${equity:.2f} | "
                    f"Realized P&L=${realized_pnl:+.2f} | Since {session}[/bold cyan]"
                )
                self._paper_starting_equity = equity
                self._paper_realized_pnl = realized_pnl
                return state
        except Exception as e:
            console.print(f"[yellow]Could not load paper state ({e}). Starting fresh at $100.[/yellow]")
        return {}

    def save_paper_state(self, equity: float, realized_pnl: float = 0.0,
                         positions: Optional[List[Dict]] = None) -> None:
        """
        Atomically persist paper trading equity + realized P&L to bridge/paper_state.json.
        Called on every dashboard tick and on clean shutdown, so restarts are non-destructive.
        """
        if not self.paper:
            return
        try:
            existing: Dict[str, Any] = {}
            if os.path.exists(self.paper_state_path):
                try:
                    with open(self.paper_state_path, "r") as f:
                        existing = json.load(f)
                except Exception:
                    pass

            state = {
                "equity": round(equity, 6),
                "realized_pnl": round(realized_pnl, 6),
                "open_positions": positions or [],
                "session_start": existing.get(
                    "session_start",
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                ),
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "total_trades": existing.get("total_trades", 0),
                "starting_balance": existing.get("starting_balance", 100.0),
            }
            tmp = self.paper_state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.paper_state_path)
            self._paper_realized_pnl = realized_pnl
        except Exception:
            pass

    def load_prospects(self) -> Dict[str, str]:
        """
        Read bridge/prospects.json and update active AI prospect biases.
        Supports dict or list format and normalizes coin symbols and biases (LONG/SHORT/NEUTRAL).
        Updates PortfolioGuard and returns the normalized bias dictionary.
        """
        biases: Dict[str, str] = {}
        if not os.path.exists(self.prospects_path):
            return biases

        try:
            with open(self.prospects_path, "r") as pf:
                data = json.load(pf)

            if isinstance(data, dict):
                if "prospects" in data and isinstance(data["prospects"], (list, dict)):
                    data = data["prospects"]

            if isinstance(data, dict):
                for key, val in data.items():
                    coin = str(key).upper().split("-")[0].split(".")[0]
                    if isinstance(val, str):
                        raw_bias = val.upper()
                    elif isinstance(val, dict):
                        raw_bias = str(val.get("bias") or val.get("direction") or "").upper()
                    else:
                        continue

                    if raw_bias in ("LONG", "BUY", "BULLISH"):
                        biases[coin] = "LONG"
                    elif raw_bias in ("SHORT", "SELL", "BEARISH"):
                        biases[coin] = "SHORT"
                    elif raw_bias in ("NEUTRAL", "FLAT", "NONE"):
                        biases[coin] = "NEUTRAL"

            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        coin = str(item.get("coin") or item.get("symbol") or item.get("name") or "").upper().split("-")[0].split(".")[0]
                        raw_bias = str(item.get("bias") or item.get("direction") or "").upper()
                        if raw_bias in ("LONG", "BUY", "BULLISH"):
                            biases[coin] = "LONG"
                        elif raw_bias in ("SHORT", "SELL", "BEARISH"):
                            biases[coin] = "SHORT"
                        elif raw_bias in ("NEUTRAL", "FLAT", "NONE"):
                            biases[coin] = "NEUTRAL"
                        if coin and raw_bias:
                            biases[coin] = biases.get(coin, raw_bias)

            if self.guard:
                self.guard.set_prospect_biases(biases)
            self.prospect_biases = biases

        except Exception:
            pass

        return biases

    def setup(self) -> None:
        """Initialize Testnet TradingNode and register all 4 strategies."""
        # Restore paper trading state from disk FIRST (before anything else)
        if self.paper:
            self.load_paper_state()

        mode_str = "MAINNET (EMULATED PAPER)" if self.paper else "TESTNET (BURNER KEY)"
        console.print(f"[bold cyan]🚀 Initializing Hyperliquid Multi-Strategy Node - {mode_str}...[/bold cyan]")
        if not self.paper:
            if self.is_ephemeral:
                console.print(f"[bold yellow]🔑 Generated ephemeral burner wallet: {self.wallet_address}[/bold yellow]")
            else:
                console.print(f"[bold green]🔑 Loaded wallet from environment: {self.wallet_address}[/bold green]")
        else:
            console.print(f"[bold yellow]🛡️ Running purely local emulated execution. No keys required![/bold yellow]")

        # Fetch top perpetuals via public unauthenticated API
        top_markets = self.info_client.get_top_perpetuals(top_n=self.top_n)
        self.top_coins = [m["name"] for m in top_markets]

        # Load and wire AI prospects from bridge/prospects.json
        self.load_prospects()
        if self.prospect_biases:
            console.print(f"[bold cyan]🎯 Loaded {len(self.prospect_biases)} AI Prospect biases: {self.prospect_biases}[/bold cyan]")
            # Ensure prospect coins are monitored and prioritized
            for p_coin in self.prospect_biases:
                if p_coin not in self.top_coins:
                    self.top_coins.append(p_coin)

        console.print(f"[green]Screened {len(self.top_coins)} top perpetuals for surveillance.[/green]")

        env = HyperliquidEnvironment.MAINNET if self.paper else HyperliquidEnvironment.TESTNET
        
        target_ids = frozenset([InstrumentId(Symbol(f"{coin}-USD-PERP"), Venue("HYPERLIQUID")) for coin in self.top_coins])
        
        data_cfg = HyperliquidDataClientConfig(
            environment=env,
            instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=target_ids),
        )
        
        exec_clients = {}
        if not self.paper:
            exec_cfg = HyperliquidExecClientConfig(
                environment=env,
                account_address=self.wallet_address,
                private_key=self._private_key,
                instrument_provider=InstrumentProviderConfig(load_all=False, load_ids=target_ids),
            )
            exec_clients["HYPERLIQUID_EXEC"] = exec_cfg
        else:
            from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
            exec_cfg = SandboxExecutionClientConfig(
                venue="HYPERLIQUID",
                starting_balances=["100 USD"],
                account_type="MARGIN",
                base_currency="USD",
            )
            exec_clients["HYPERLIQUID_EXEC"] = exec_cfg

        node_config = TradingNodeConfig(
            trader_id=self.trader_id,
            data_clients={"HYPERLIQUID_DATA": data_cfg},
            exec_clients=exec_clients,
            emulator=OrderEmulatorConfig(),
            logging=LoggingConfig(log_level="ERROR"),
        )

        self.node = TradingNode(config=node_config)
        del self._private_key
        self.node.add_data_client_factory("HYPERLIQUID_DATA", HyperliquidLiveDataClientFactory)
        if not self.paper:
            self.node.add_exec_client_factory("HYPERLIQUID_EXEC", HyperliquidLiveExecClientFactory)
        else:
            from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
            self.node.add_exec_client_factory("HYPERLIQUID_EXEC", SandboxLiveExecClientFactory)

        console.print("[yellow]Building TradingNode core actor network and message bus...[/yellow]")
        self.node.build()

        if self.paper:
            import time as _time
            from nautilus_trader.core.uuid import UUID4
            from nautilus_trader.model.identifiers import AccountId
            from nautilus_trader.model.enums import AccountType
            from nautilus_trader.model.currencies import USD
            from nautilus_trader.model.objects import MarginBalance, Money, AccountBalance
            from nautilus_trader.model.events.account import AccountState

            # Use persisted equity so restarts don't wipe paper P&L
            restored_equity = self._paper_starting_equity
            mock_state = AccountState(
                AccountId("HYPERLIQUID-PAPER001"),
                AccountType.MARGIN,
                USD,
                False,
                [AccountBalance(Money(restored_equity, USD), Money(0.0, USD), Money(restored_equity, USD))],
                [MarginBalance(Money(restored_equity, USD), Money(restored_equity, USD))],
                {},
                UUID4(),
                int(_time.time() * 10**9),
                int(_time.time() * 10**9)
            )
            self.node.portfolio.update_account(mock_state)
            console.print(f"[bold green]💾 Paper account seeded at ${restored_equity:.2f} (restored from saved state)[/bold green]")

        



        # Instantiate all 4 Strategy Actors attached to shared PortfolioGuard
        self.continuation_strat = TrendContinuationSMC(
            config=TrendContinuationConfig(venue="HYPERLIQUID", risk_per_trade_pct=self.risk_pct, reward_to_risk_ratio=self.rr_ratio),
            portfolio_guard=self.guard,
        )
        self.funding_strat = HourlyFundingFade(
            config=HourlyFundingFadeConfig(venue="HYPERLIQUID", risk_per_trade_pct=min(self.risk_pct, 0.0075)),
            portfolio_guard=self.guard,
        )
        self.scalp_strat = OrderBookImbalance(
            config=OrderBookImbalanceConfig(venue="HYPERLIQUID", risk_per_trade_pct=min(self.risk_pct, 0.005)),
            portfolio_guard=self.guard,
        )
        self.vwap_strat = VwapOiMomentum(
            config=VwapOiMomentumConfig(venue="HYPERLIQUID", risk_per_trade_pct=self.risk_pct),
            portfolio_guard=self.guard,
        )

        # Register strategies with Nautilus node trader
        self.node.trader.add_strategy(self.continuation_strat)
        self.node.trader.add_strategy(self.funding_strat)
        self.node.trader.add_strategy(self.scalp_strat)
        self.node.trader.add_strategy(self.vwap_strat)

        console.print("[bold green]✅ All 4 modular strategies registered under PortfolioGuard![/bold green]")

    def run(self, max_seconds: Optional[int] = None) -> None:
        """Run the live surveillance and risk monitoring loop."""
        if self.node is None:
            self.setup()

        self._is_running = True

        def signal_handler(sig, frame):
            console.print("\n[bold red]Shutdown signal received. Gracefully stopping TradingNode...[/bold red]")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        start_time = time.time()

        # Terminal surveillance loop
        with Live(
            create_surveillance_table(
                self.top_coins,
                self.continuation_strat,
                self.funding_strat,
                self.scalp_strat,
                self.vwap_strat,
                self.guard,
                self.info_client,
                self.node,
                self.wallet_address,
                self.is_ephemeral,
                self.paper,
            ),
            refresh_per_second=1,
            console=console,
        ) as live:
            while self._is_running:
                # Dynamic AI prospects reload from bridge
                self.load_prospects()

                # Cancel any stale limit orders that exceeded timeout
                stale_orders = self.guard.get_stale_orders_to_cancel()
                for stale in stale_orders:
                    console.print(f"[yellow]Cancelling stale order {stale.order_id} on {stale.instrument_id}[/yellow]")
                    try:
                        cancel_cmd = CancelOrder(
                            trader_id=self.node.trader_id,
                            strategy_id=StrategyId(stale.strategy_id),
                            instrument_id=stale.instrument_id,
                            client_order_id=ClientOrderId(stale.order_id),
                        )
                        self.node.trader.execute(cancel_cmd)
                    except Exception as e:
                        console.print(f"[red]Failed to cancel stale order {stale.order_id}: {e}[/red]")
                    self.guard.acknowledge_order_cancelled(stale.order_id)

                live.update(
                    create_surveillance_table(
                        self.top_coins,
                        self.continuation_strat,
                        self.funding_strat,
                        self.scalp_strat,
                        self.vwap_strat,
                        self.guard,
                        self.info_client,
                        self.node,
                        self.wallet_address,
                        self.is_ephemeral,
                        self.paper,
                    )
                )
                time.sleep(1.0)
                if max_seconds and (time.time() - start_time) >= max_seconds:
                    console.print(f"[yellow]Execution duration {max_seconds}s reached.[/yellow]")
                    break

        self.stop()

    def stop(self) -> None:
        """Clean shutdown of node."""
        self._is_running = False
        if self.node:
            try:
                self.node.stop()
                self.node.dispose()
                console.print("[green]TradingNode stopped and disposed cleanly.[/green]")
            except Exception as e:
                console.print(f"[red]Error during node stop: {e}[/red]")


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid Multi-Strategy Live / Testnet TradingNode Runner")
    parser.add_argument("--top-n", type=int, default=20, help="Number of perpetual markets to monitor")
    parser.add_argument("--duration", type=int, default=None, help="Optional duration in seconds (for test/demo)")
    parser.add_argument("--risk-pct", type=float, default=0.01, help="Risk percentage per trade")
    parser.add_argument("--rr-ratio", type=float, default=2.5, help="Reward-to-risk ratio")
    parser.add_argument("--paper", action="store_true", help="Use local OrderEmulator on Mainnet data instead of Testnet Execution")
    args = parser.parse_args()

    runner = HyperliquidNodeRunner(
        top_n=args.top_n,
        risk_pct=args.risk_pct,
        rr_ratio=args.rr_ratio,
        paper=args.paper,
    )
    runner.setup()
    runner.run(max_seconds=args.duration)


if __name__ == "__main__":
    main()
