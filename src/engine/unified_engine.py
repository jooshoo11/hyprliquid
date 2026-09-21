"""
Unified Autonomous Trading Engine (src/engine/unified_engine.py)
A self-healing, fully autonomous multi-strategy trading daemon providing:
1. Nautilus Live TradingNode:
   - Live Mainnet data surveillance across top perpetuals.
   - 4 Strategy Actors (SMC Trend Continuation, Hourly Funding Fade, Book Imbalance, VWAP/OI Momentum).
   - PortfolioGuard risk engine enforcing coin-specific exchange max leverage and 20% daily DD circuit breaker.
2. In-Process AI Sentinel:
   - 5-10s watchdog: audits open positions against live L2 order book depth and funding APR.
   - Natively triggers immediate strategy.close_position() on toxic conditions (zero lag, zero file bridges).
   - 15m prospector: scans top 50 markets by volume & carry, sets high-conviction biases.
3. In-Process Web Cockpit Integration:
   - Exposes in-memory state to FastAPI / WebSockets for the modern dark-mode browser dashboard.
   - 1-click web closes execute directly in memory.
4. Auto-Persistence:
   - Maintains paper equity and session history across restarts.
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import os
import time
import json
import threading
import signal
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

import polars as pl
from rich.console import Console

from nautilus_trader.live.node import TradingNode
from nautilus_trader.config import TradingNodeConfig, OrderEmulatorConfig, LoggingConfig
from nautilus_trader.adapters.hyperliquid.factories import (
    HyperliquidLiveDataClientFactory,
    HyperliquidLiveExecClientFactory,
)
from nautilus_trader.adapters.hyperliquid.config import (
    HyperliquidDataClientConfig,
    HyperliquidExecClientConfig,
    HyperliquidEnvironment,
)
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue, StrategyId, AccountId
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.objects import MarginBalance, Money, AccountBalance
from nautilus_trader.model.events.account import AccountState
from nautilus_trader.core.uuid import UUID4

from src.strategies.continuation import TrendContinuationSMC, TrendContinuationConfig
from src.strategies.funding_fade import HourlyFundingFade, HourlyFundingFadeConfig
from src.strategies.orderbook_scalp import OrderBookImbalance, OrderBookImbalanceConfig
from src.strategies.vwap_momentum import VwapOiMomentum, VwapOiMomentumConfig
from src.risk.portfolio_guard import PortfolioGuard
from src.scanner.mcp_client import HyperliquidInfoClient
from src.utils.instruments import get_coin_max_leverage
from src.execution.node_runner import generate_or_load_wallet

console = Console()


class UnifiedEngine:
    """
    Master Autonomous Engine coordinating TradingNode, AI Sentinel, and Web API.
    """

    def __init__(
        self,
        top_n: int = 30,
        paper: bool = True,
        risk_pct: float = 0.01,
        rr_ratio: float = 2.5,
        web_port: int = 8000,
    ):
        self.top_n = top_n
        self.paper = paper
        self.risk_pct = risk_pct
        self.rr_ratio = rr_ratio
        self.web_port = web_port

        self.info_client = HyperliquidInfoClient(network="mainnet" if paper else "testnet")
        self.wallet_address, self._private_key, self.is_ephemeral = generate_or_load_wallet()

        self.guard = PortfolioGuard(
            max_strategy_equity_pct=25.0,
            max_total_open_positions=10,
            max_daily_drawdown_pct=0.20,
        )

        self.node: Optional[TradingNode] = None
        self.continuation_strat: Optional[TrendContinuationSMC] = None
        self.funding_strat: Optional[HourlyFundingFade] = None
        self.scalp_strat: Optional[OrderBookImbalance] = None
        self.vwap_strat: Optional[VwapOiMomentum] = None

        self.top_coins: List[str] = []
        self.prospects: Dict[str, Any] = {}
        self.audit_events: List[Dict[str, Any]] = []

        self._is_running = False
        self._threads: List[threading.Thread] = []

        # State persistence paths
        self.paper_state_path = os.path.join(REPO_ROOT, "bridge", "paper_state.json")
        self.active_trades_path = os.path.join(REPO_ROOT, "bridge", "active_trades.json")
        self.prospects_path = os.path.join(REPO_ROOT, "bridge", "prospects.json")
        self.ai_commands_path = os.path.join(REPO_ROOT, "bridge", "ai_commands.json")

        self._paper_starting_equity = 100.0
        self._paper_realized_pnl = 0.0

    def log(self, message: str, level: str = "INFO") -> None:
        """Structured logging with in-memory ring buffer for the web UI."""
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        event = {
            "timestamp": timestamp,
            "level": level,
            "message": message,
        }
        self.audit_events.append(event)
        if len(self.audit_events) > 100:
            self.audit_events.pop(0)

        color_map = {"INFO": "cyan", "WARN": "yellow", "ERROR": "red", "TRIGGER": "bold magenta", "CLOSE": "bold green"}
        c = color_map.get(level, "white")
        console.print(f"[{c}][{timestamp}] [{level}] {message}[/{c}]")

    def load_paper_state(self) -> None:
        """Load persisted equity from bridge/paper_state.json."""
        if not self.paper:
            return
        try:
            if os.path.exists(self.paper_state_path):
                with open(self.paper_state_path, "r") as f:
                    state = json.load(f)
                self._paper_starting_equity = float(state.get("equity", 100.0))
                self._paper_realized_pnl = float(state.get("realized_pnl", 0.0))
                self.log(f"Restored paper state: Equity=${self._paper_starting_equity:.2f} | Realized P&L=${self._paper_realized_pnl:+.2f}")
        except Exception as e:
            self.log(f"Paper state init fresh at $100 ({e})", "WARN")

    def save_paper_state(self, equity: float, realized_pnl: float, positions: List[Dict]) -> None:
        """Atomically persist paper equity to disk."""
        if not self.paper:
            return
        try:
            state = {
                "equity": round(equity, 2),
                "realized_pnl": round(realized_pnl, 2),
                "open_positions": positions,
                "session_start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "total_trades": 0,
                "starting_balance": 100.0,
            }
            tmp = f"{self.paper_state_path}.tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.paper_state_path)
        except Exception:
            pass

    def setup(self) -> None:
        """Initialize Nautilus TradingNode and Strategy Actors."""
        self.load_paper_state()
        mode_str = "MAINNET (LOCAL EMULATOR)" if self.paper else "TESTNET (BURNER KEY)"
        self.log(f"🚀 Initializing Unified Autonomous Trading Engine - {mode_str}...")

        # Screen top perpetuals
        top_markets = self.info_client.get_top_perpetuals(top_n=self.top_n)
        self.top_coins = [m["name"] for m in top_markets]
        self.log(f"Surveillance configured across {len(self.top_coins)} top perpetuals.")

        env = HyperliquidEnvironment.MAINNET if self.paper else HyperliquidEnvironment.TESTNET
        target_ids = frozenset([InstrumentId(Symbol(f"{c}-USD-PERP"), Venue("HYPERLIQUID")) for c in self.top_coins])

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
            exec_cfg = SandboxExecutionClientConfig(
                venue="HYPERLIQUID",
                starting_balances=[f"{self._paper_starting_equity} USD"],
                account_type="MARGIN",
                base_currency="USD",
            )
            exec_clients["HYPERLIQUID_EXEC"] = exec_cfg

        node_config = TradingNodeConfig(
            trader_id="HL-AUTONOMOUS-001",
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
            self.node.add_exec_client_factory("HYPERLIQUID_EXEC", SandboxLiveExecClientFactory)

        self.node.build()

        if self.paper:
            restored_equity = self._paper_starting_equity
            mock_state = AccountState(
                AccountId("HYPERLIQUID-001"),
                AccountType.MARGIN,
                USD,
                False,
                [AccountBalance(Money(restored_equity, USD), Money(0.0, USD), Money(restored_equity, USD))],
                [MarginBalance(Money(restored_equity, USD), Money(restored_equity, USD))],
                {},
                UUID4(),
                int(time.time() * 10**9),
                int(time.time() * 10**9),
            )
            self.node.portfolio.update_account(mock_state)

        # Instantiate all 4 Strategy Actors
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

        self.node.trader.add_strategy(self.continuation_strat)
        self.node.trader.add_strategy(self.funding_strat)
        self.node.trader.add_strategy(self.scalp_strat)
        self.node.trader.add_strategy(self.vwap_strat)

        self.log("✅ All 4 modular strategies registered under PortfolioGuard.", "INFO")

    def get_open_positions(self) -> List[Any]:
        """Fetch active open positions directly from Nautilus cache."""
        try:
            if self.node and self.node.cache:
                return [p for p in self.node.cache.positions_open() if not p.is_closed]
        except Exception:
            pass
        return []

    def get_account_cash(self) -> float:
        """Fetch current cash balance from portfolio."""
        try:
            if self.node and self.node.portfolio:
                venue = Venue("HYPERLIQUID")
                acct = self.node.portfolio.account(venue=venue)
                if acct:
                    bal = acct.balance_total(USD)
                    if bal:
                        return bal.as_double()
        except Exception:
            pass
        return self._paper_starting_equity

    def get_state(self) -> Dict[str, Any]:
        """Assemble live portfolio, trade, and AI state directly from in-memory engine."""
        cash_balance = self.get_account_cash()
        open_positions = self.get_open_positions()

        # Fetch live prices
        px_map = {}
        try:
            meta, asset_ctxs = self.info_client.get_meta_and_asset_ctxs()
            universe = meta.get("universe", [])
            for u, ctx in zip(universe, asset_ctxs):
                px_map[u.get("name")] = float(ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx", 0.0))
        except Exception:
            pass

        positions_data = []
        total_unrealized = 0.0
        total_notional = 0.0

        for pos in open_positions:
            try:
                coin = pos.instrument_id.symbol.value.split("-")[0]
                entry_px = float(pos.avg_px_open) if hasattr(pos, "avg_px_open") else (pos.avg_px.as_double() if pos.avg_px else 0.0)
                cur_px = px_map.get(coin, entry_px)
                qty = pos.quantity.as_double()
                side = "LONG" if pos.is_long else "SHORT"

                is_long = pos.is_long
                pnl = (cur_px - entry_px) * qty * (1.0 if is_long else -1.0) if (cur_px > 0 and entry_px > 0) else 0.0
                total_unrealized += pnl
                total_notional += (cur_px * qty)

                positions_data.append({
                    "coin": coin,
                    "instrument_id": str(pos.instrument_id),
                    "side": side,
                    "size": qty,
                    "entry_price": entry_px,
                    "mark_price": cur_px,
                    "unrealized_pnl": round(pnl, 2),
                    "roi_pct": round((pnl / (entry_px * qty) * 100.0) if (entry_px * qty) > 0 else 0.0, 2),
                })
            except Exception:
                pass

        total_equity = round(cash_balance + total_unrealized, 2)
        roi_total = round((total_unrealized / cash_balance * 100.0) if cash_balance > 0 else 0.0, 2)

        # Format prospects
        prospects_list = []
        for coin, p_data in self.prospects.items():
            prospects_list.append({
                "coin": coin,
                "bias": p_data.get("bias", "NEUTRAL"),
                "target_entry": p_data.get("target_entry", 0.0),
                "rationale": p_data.get("reason", ""),
                "change_24h": p_data.get("change_24h", 0.0),
                "volume_24h": p_data.get("volume_24h", 0.0),
            })

        return {
            "timestamp": time.time(),
            "equity": total_equity,
            "cash_balance": round(cash_balance, 2),
            "net_unrealized": round(total_unrealized, 2),
            "notional_exposure": round(total_notional, 2),
            "roi_pct": roi_total,
            "positions": positions_data,
            "prospects": prospects_list,
            "audit_events": self.audit_events[-20:],
            "circuit_breaker": "TRIPPED" if self.guard.is_circuit_breaker_triggered else "NORMAL",
            "paper": self.paper,
        }

    def close_position(self, coin: str, reason: str = "Manual Close") -> bool:
        """Natively and immediately close an open position across any strategy."""
        coin_clean = coin.upper().split("-")[0].split(".")[0]
        instr_id = InstrumentId(Symbol(f"{coin_clean}-USD-PERP"), Venue("HYPERLIQUID"))

        if not self.node or not self.node.cache:
            return False

        open_positions = [p for p in self.node.cache.positions_open() if not p.is_closed and p.instrument_id == instr_id]
        if not open_positions:
            self.log(f"No active position found to close for {coin_clean}", "WARN")
            return False

        closed_any = False
        for pos in open_positions:
            for strat in [self.continuation_strat, self.funding_strat, self.scalp_strat, self.vwap_strat]:
                if strat and strat.id == pos.strategy_id:
                    strat.close_position(pos)
                    closed_any = True
                    self.log(f"Market close submitted for {coin_clean} via {strat.id} ({reason})", "CLOSE")
                    break
            if not closed_any:
                fallback = self.funding_strat or self.continuation_strat
                if fallback:
                    fallback.close_position(pos)
                    closed_any = True
                    self.log(f"Market close submitted for {coin_clean} via fallback {fallback.id} ({reason})", "CLOSE")

        return closed_any

    def close_all_positions(self, reason: str = "Emergency Close All") -> List[str]:
        """Emergency market close across all open positions."""
        open_positions = self.get_open_positions()
        closed = []
        for pos in open_positions:
            coin = pos.instrument_id.symbol.value.split("-")[0]
            if self.close_position(coin, reason=reason):
                closed.append(coin)
        return closed

    def _ai_sentinel_watchdog_loop(self) -> None:
        """In-process real-time trade sentry auditing active positions every 5s."""
        self.log("🛡️ [AI Sentinel] Real-time active trade watchdog started (5s cycle).", "INFO")
        while self._is_running:
            try:
                open_positions = self.get_open_positions()
                if open_positions:
                    for pos in open_positions:
                        coin = pos.instrument_id.symbol.value.split("-")[0]
                        side = "LONG" if pos.is_long else "SHORT"

                        # 1. Audit L2 orderbook depth skew
                        l2 = self.info_client.get_l2_snapshot(coin)
                        if l2.get("status") == "SUCCESS":
                            bids = l2.get("bids", [])
                            asks = l2.get("asks", [])
                            bids_depth = sum(float(b["sz"]) * float(b["px"]) for b in bids[:5])
                            asks_depth = sum(float(a["sz"]) * float(a["px"]) for a in asks[:5])

                            # Adverse depth wall collapse (>4x depth against position)
                            if side == "SHORT" and asks_depth > 0 and (bids_depth / asks_depth) > 4.0:
                                reason = f"Orderbook depth wall collapsed: Bid depth ${bids_depth/1e3:.1f}k > 4x Ask depth ${asks_depth/1e3:.1f}k"
                                self.log(f"🚨 [AI Sentinel] EMERGENCY TRIGGER: {coin} - {reason}", "TRIGGER")
                                self.close_position(coin, reason=reason)
                                continue

                            if side == "LONG" and bids_depth > 0 and (asks_depth / bids_depth) > 4.0:
                                reason = f"Orderbook depth wall collapsed: Ask depth ${asks_depth/1e3:.1f}k > 4x Bid depth ${bids_depth/1e3:.1f}k"
                                self.log(f"🚨 [AI Sentinel] EMERGENCY TRIGGER: {coin} - {reason}", "TRIGGER")
                                self.close_position(coin, reason=reason)
                                continue

                        # 2. Adverse funding rate spike (>120% APR)
                        funding_apr = self.info_client.get_funding_rate(coin)
                        if funding_apr is not None:
                            if side == "LONG" and funding_apr > 1.20:
                                reason = f"Adverse funding spike to +{funding_apr*100:.1f}% APR (toxic long carry)"
                                self.log(f"🚨 [AI Sentinel] EMERGENCY TRIGGER: {coin} - {reason}", "TRIGGER")
                                self.close_position(coin, reason=reason)
                                continue
                            if side == "SHORT" and funding_apr < -1.20:
                                reason = f"Adverse funding drop to {funding_apr*100:.1f}% APR (toxic short carry)"
                                self.log(f"🚨 [AI Sentinel] EMERGENCY TRIGGER: {coin} - {reason}", "TRIGGER")
                                self.close_position(coin, reason=reason)
                                continue

                # Also process any bridge commands if written externally
                self._check_external_bridge_commands()

            except Exception as e:
                self.log(f"AI Sentinel watchdog loop notice: {e}", "WARN")

            time.sleep(5.0)

    def _check_external_bridge_commands(self) -> None:
        """Execute any commands queued in bridge/ai_commands.json."""
        if not os.path.exists(self.ai_commands_path):
            return
        try:
            with open(self.ai_commands_path, "r") as f:
                cmds = json.load(f)
            if not cmds:
                return

            for cmd in cmds:
                if cmd.get("action") == "CLOSE_POSITION":
                    coin = cmd.get("coin")
                    reason = cmd.get("reason", "Bridge Close Command")
                    self.close_position(coin, reason=reason)

            with open(self.ai_commands_path, "w") as f:
                json.dump([], f)
        except Exception:
            pass

    def _ai_prospector_loop(self) -> None:
        """In-process market prospector scanning top 50 markets every 15m."""
        self.log("🔍 [AI Prospector] High-frequency scanner started (15m cycle).", "INFO")
        while self._is_running:
            try:
                meta, asset_ctxs = self.info_client.get_meta_and_asset_ctxs()
                universe = meta.get("universe", [])
                records = []
                for u, ctx in zip(universe, asset_ctxs):
                    name = u.get("name")
                    px = float(ctx.get("oraclePx", 0.0))
                    prev_px = float(ctx.get("prevDayPx", 0.0))
                    funding = float(ctx.get("funding", 0.0)) * 24 * 365
                    vol_24h = float(ctx.get("dayNtlVlm", 0.0))
                    change_24h = ((px - prev_px) / prev_px * 100) if prev_px > 0 else 0.0
                    records.append({
                        "coin": name, "price": px, "funding_apr": funding,
                        "vol_24h": vol_24h, "change_24h": change_24h,
                    })

                df = pl.DataFrame(records).sort("vol_24h", descending=True).head(50)
                prospects = {}

                # 1. Spot-led long momentum
                longs = df.filter((pl.col("change_24h") > 3.0) & (pl.col("funding_apr") < 25.0)).sort("vol_24h", descending=True).head(5)
                for row in longs.iter_rows(named=True):
                    prospects[row["coin"]] = {
                        "bias": "LONG",
                        "target_entry": row["price"],
                        "reason": f"Spot-led momentum: +{row['change_24h']:.1f}% 24h on ${row['vol_24h']/1e6:.1f}M vol; healthy low funding APR +{row['funding_apr']:.1f}%.",
                        "change_24h": row["change_24h"],
                        "volume_24h": row["vol_24h"],
                    }

                # 2. Crowded long fade
                shorts = df.filter(pl.col("funding_apr") > 90.0).sort("funding_apr", descending=True).head(5)
                for row in shorts.iter_rows(named=True):
                    prospects[row["coin"]] = {
                        "bias": "SHORT",
                        "target_entry": row["price"],
                        "reason": f"Crowded long fade: Funding APR +{row['funding_apr']:.1f}% on ${row['vol_24h']/1e6:.1f}M vol (+{row['change_24h']:.1f}% 24h); longs overleveraged.",
                        "change_24h": row["change_24h"],
                        "volume_24h": row["vol_24h"],
                    }

                self.prospects = prospects
                biases = {c: p["bias"] for c, p in prospects.items()}
                self.guard.set_prospect_biases(biases)
                self.log(f"🎯 [AI Prospector] Updated {len(prospects)} high-conviction targets: {list(prospects.keys())}", "INFO")

                # Sync to bridge/prospects.json
                with open(self.prospects_path, "w") as f:
                    json.dump(prospects, f, indent=2)

            except Exception as e:
                self.log(f"Prospector scan warning: {e}", "WARN")

            # Wait 15 minutes or until stopped
            for _ in range(900):
                if not self._is_running:
                    break
                time.sleep(1.0)

    def _sync_bridge_state_loop(self) -> None:
        """Sync in-memory state to disk every 1s for persistence."""
        while self._is_running:
            try:
                state = self.get_state()
                # 1. Update bridge/active_trades.json
                tmp_act = f"{self.active_trades_path}.tmp"
                with open(tmp_act, "w") as f:
                    json.dump({
                        "timestamp": state["timestamp"],
                        "equity": state["equity"],
                        "cash_balance": state["cash_balance"],
                        "positions": state["positions"],
                    }, f, indent=2)
                os.replace(tmp_act, self.active_trades_path)

                # 2. Persist paper state
                if self.paper:
                    self.save_paper_state(
                        equity=state["equity"],
                        realized_pnl=state["cash_balance"] - 100.0,
                        positions=state["positions"],
                    )
            except Exception:
                pass
            time.sleep(1.0)

    def start(self) -> None:
        """Start the complete autonomous engine and all embedded sub-services."""
        if self.node is None:
            self.setup()

        self._is_running = True

        # 1. Start Nautilus TradingNode core loop in thread
        def node_thread():
            try:
                self.node.run()
            except Exception as e:
                self.log(f"Node execution ended: {e}", "INFO")

        t_node = threading.Thread(target=node_thread, daemon=True, name="NautilusNode")
        t_node.start()
        self._threads.append(t_node)

        # 2. Start In-Process AI Sentinel Watchdog
        t_sentinel = threading.Thread(target=self._ai_sentinel_watchdog_loop, daemon=True, name="AISentinel")
        t_sentinel.start()
        self._threads.append(t_sentinel)

        # 4. Start In-Process AI Prospector
        t_prospector = threading.Thread(target=self._ai_prospector_loop, daemon=True, name="AIProspector")
        t_prospector.start()
        self._threads.append(t_prospector)

        # 5. Start State Sync Loop
        t_sync = threading.Thread(target=self._sync_bridge_state_loop, daemon=True, name="StateSync")
        t_sync.start()
        self._threads.append(t_sync)

        self.log(f"🌟 Unified Autonomous Engine fully running! Web Cockpit on http://0.0.0.0:{self.web_port}", "INFO")

    def stop(self) -> None:
        """Gracefully shut down all engine systems."""
        self.log("🛑 Stopping Unified Autonomous Engine gracefully...", "INFO")
        self._is_running = False

        # Persist final state
        try:
            state = self.get_state()
            self.save_paper_state(state["equity"], state["cash_balance"] - 100.0, state["positions"])
        except Exception:
            pass

        if self.node:
            try:
                self.node.stop()
                self.node.dispose()
            except Exception:
                pass

        self.log("TradingNode disposed cleanly. Shutdown complete.", "INFO")
