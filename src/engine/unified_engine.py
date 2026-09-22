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
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue, StrategyId, AccountId, ClientOrderId
from nautilus_trader.execution.messages import CancelOrder
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OrderSide
from nautilus_trader.model.objects import MarginBalance, Money, AccountBalance
from nautilus_trader.model.events.account import AccountState
from nautilus_trader.core.uuid import UUID4

from src.strategies.continuation import TrendContinuationSMC, TrendContinuationConfig
from src.strategies.funding_fade import HourlyFundingFade, HourlyFundingFadeConfig
from src.strategies.orderbook_scalp import OrderBookImbalance, OrderBookImbalanceConfig
from src.strategies.vwap_momentum import VwapOiMomentum, VwapOiMomentumConfig
from src.risk.portfolio_guard import PortfolioGuard
from src.risk.trade_manager import TradeManager, TradeAction
from src.risk.performance_analytics import PerformanceAnalytics
from src.scanner.mcp_client import HyperliquidInfoClient
from src.scanner.arbitrage_scanner import ArbitrageScanner
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
        self.trade_manager = TradeManager(
            min_holding_seconds=60.0,
            reentry_cooldown_seconds=60.0,
            breakeven_roi_pct=1.0,
            trailing_roi_pct=2.0,
            mae_roi_pct=-2.5,
            mae_loss_usd=-10.0,
        )
        self.analytics = PerformanceAnalytics()

        self.node: Optional[TradingNode] = None
        self.continuation_strat: Optional[TrendContinuationSMC] = None
        self.funding_strat: Optional[HourlyFundingFade] = None
        self.scalp_strat: Optional[OrderBookImbalance] = None
        self.vwap_strat: Optional[VwapOiMomentum] = None

        self.top_coins: List[str] = []
        self.prospects: Dict[str, Any] = {}
        self.funding_arbitrage_pairs: List[Dict[str, Any]] = []
        self.audit_events: List[Dict[str, Any]] = []
        self.sentinel_thoughts: List[Dict[str, Any]] = []

        self.arbitrage_scanner = ArbitrageScanner(info_client=self.info_client)

        self._is_running = False
        self._threads: List[threading.Thread] = []

        # State persistence paths
        self.paper_state_path = os.path.join(REPO_ROOT, "bridge", "paper_state.json")
        self.active_trades_path = os.path.join(REPO_ROOT, "bridge", "active_trades.json")
        self.prospects_path = os.path.join(REPO_ROOT, "bridge", "prospects.json")
        self.funding_arbitrage_path = os.path.join(REPO_ROOT, "bridge", "funding_arbitrage.json")
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
        self.guard.update_dynamic_allocations(self.trade_manager.get_closed_trades())
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
            info_client=self.info_client,
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

                strat_raw = str(pos.strategy_id) if hasattr(pos, "strategy_id") and pos.strategy_id else ""
                strat_name = "SMC Trend"
                if "Funding" in strat_raw:
                    strat_name = "Funding Fade"
                elif "OrderBook" in strat_raw or "Scalp" in strat_raw or "Imbalance" in strat_raw:
                    strat_name = "Book Imbalance"
                elif "Vwap" in strat_raw or "Momentum" in strat_raw:
                    strat_name = "VWAP/OI"
                elif "Continuation" in strat_raw or "SMC" in strat_raw:
                    strat_name = "SMC Trend"

                positions_data.append({
                    "coin": coin,
                    "instrument_id": str(pos.instrument_id),
                    "side": side,
                    "size": qty,
                    "entry_price": entry_px,
                    "mark_price": cur_px,
                    "unrealized_pnl": round(pnl, 2),
                    "roi_pct": round((pnl / (entry_px * qty) * 100.0) if (entry_px * qty) > 0 else 0.0, 2),
                    "strategy": strat_name,
                })
            except Exception:
                pass

        total_equity = round(cash_balance + total_unrealized, 2)
        roi_total = round((total_unrealized / cash_balance * 100.0) if cash_balance > 0 else 0.0, 2)

        # Format prospects: load from bridge/prospects.json if present
        prospects_source = self.prospects
        if os.path.exists(self.prospects_path):
            try:
                with open(self.prospects_path, "r") as f:
                    p_json = json.load(f)
                    if isinstance(p_json, dict) and "prospects" in p_json:
                        prospects_source = p_json["prospects"]
                    elif isinstance(p_json, dict) and p_json:
                        prospects_source = p_json
            except Exception:
                pass

        prospects_list = []
        if isinstance(prospects_source, list):
            for p in prospects_source:
                if isinstance(p, dict):
                    prospects_list.append({
                        "coin": p.get("coin", "UNKNOWN"),
                        "bias": p.get("bias", "NEUTRAL"),
                        "score": p.get("score", 0.0),
                        "target_entry": p.get("target_entry", 0.0),
                        "rationale": p.get("reason") or p.get("rationale", ""),
                        "change_24h": p.get("change_24h", 0.0),
                        "volume_24h": p.get("volume_24h", p.get("volume_24h_usd", 0.0)),
                        "funding_apr": p.get("funding_apr", p.get("funding_apr_pct", 0.0)),
                        "mark_price": p.get("mark_price", 0.0),
                    })
        elif isinstance(prospects_source, dict):
            for coin, p_data in prospects_source.items():
                if isinstance(p_data, dict):
                    prospects_list.append({
                        "coin": coin,
                        "bias": p_data.get("bias", "NEUTRAL"),
                        "score": p_data.get("score", 0.0),
                        "target_entry": p_data.get("target_entry", 0.0),
                        "rationale": p_data.get("reason") or p_data.get("rationale", ""),
                        "change_24h": p_data.get("change_24h", 0.0),
                        "volume_24h": p_data.get("volume_24h", p_data.get("volume_24h_usd", 0.0)),
                        "funding_apr": p_data.get("funding_apr", p_data.get("funding_apr_pct", 0.0)),
                        "mark_price": p_data.get("mark_price", 0.0),
                    })

        # Load funding arbitrage opportunities
        funding_arb_pairs = self.funding_arbitrage_pairs
        if not funding_arb_pairs and os.path.exists(self.funding_arbitrage_path):
            try:
                with open(self.funding_arbitrage_path, "r") as f:
                    f_data = json.load(f)
                    funding_arb_pairs = f_data.get("pairs", [])
            except Exception:
                pass

        return {
            "timestamp": time.time(),
            "equity": total_equity,
            "cash_balance": round(cash_balance, 2),
            "net_unrealized": round(total_unrealized, 2),
            "notional_exposure": round(total_notional, 2),
            "roi_pct": roi_total,
            "positions": positions_data,
            "prospects": prospects_list,
            "funding_arbitrage": funding_arb_pairs,
            "sentinel_thoughts": self.sentinel_thoughts[-25:],
            "audit_events": self.audit_events[-20:],
            "circuit_breaker": "TRIPPED" if self.guard.is_circuit_breaker_triggered else "NORMAL",
            "paper": self.paper,
            "trade_manager": self.trade_manager.get_summary(),
            "performance_metrics": self.analytics.get_metrics(self.trade_manager.get_closed_trades()),
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

            if closed_any:
                entry_px = float(pos.avg_px_open) if hasattr(pos, "avg_px_open") and pos.avg_px_open else (pos.avg_px.as_double() if hasattr(pos, "avg_px") and pos.avg_px else 0.0)
                qty = pos.quantity.as_double() if hasattr(pos, "quantity") and hasattr(pos.quantity, "as_double") else 1.0
                side = "LONG" if pos.is_long else "SHORT"
                strat_id = str(pos.strategy_id)
                exit_px = entry_px
                tracker = self.trade_manager.get_position(coin_clean)
                if tracker and tracker.current_price > 0:
                    exit_px = tracker.current_price

                self.trade_manager.close_and_journal_position(
                    coin=coin_clean,
                    exit_price=exit_px,
                    reason=reason,
                    fallback_side=side,
                    fallback_size=qty,
                    fallback_entry_price=entry_px,
                    fallback_strategy=strat_id,
                )

                # Set 3-minute anti-churn cooldown
                if hasattr(self, "guard") and self.guard:
                    self.guard.set_cooldown(coin_clean, 180.0)
                if hasattr(self, "portfolio_guard") and self.portfolio_guard:
                    self.portfolio_guard.set_cooldown(coin_clean, 180.0)
                if hasattr(self, "trade_manager") and self.trade_manager:
                    self.trade_manager.cooldown_tracker[coin_clean] = time.time() + 180.0

                # Dynamically adjust risk allocations based on updated trade performance
                if hasattr(self, "guard") and self.guard:
                    self.guard.update_dynamic_allocations(self.trade_manager.get_closed_trades())

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

    def log_missed_opportunity(
        self,
        coin: str,
        strategy: str,
        reason: str,
        metrics: Optional[Dict[str, Any]] = None,
        retrospective_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Log a missed trading opportunity or rejected trade decision via TradeManager."""
        return self.trade_manager.log_missed_opportunity(
            coin=coin,
            strategy=strategy,
            reason=reason,
            metrics=metrics,
            retrospective_note=retrospective_note,
        )

    def get_missed_opportunities(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve latest missed opportunities via TradeManager."""
        return self.trade_manager.get_missed_opportunities(limit=limit)

    def get_closed_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve recent closed trades via TradeManager."""
        return self.trade_manager.get_closed_trades(limit=limit)

    def is_in_cooldown(self, coin: str) -> bool:
        """Check if coin is in re-entry cooldown period after closing."""
        return self.trade_manager.is_in_cooldown(coin)

    def find_capital_rotation_candidate(self, new_prospect_coin: str, conviction_score: float = 0.0) -> Optional[Tuple[str, str]]:
        """Identify candidate position to close for capital rotation into a high-conviction prospect."""
        return self.trade_manager.find_capital_rotation_candidate(
            new_prospect_coin=new_prospect_coin,
            new_prospect_conviction_score=conviction_score,
            max_positions=self.guard.max_total_open_positions,
        )

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Retrieve quantitative performance metrics from closed trades."""
        return self.analytics.get_metrics(self.trade_manager.get_closed_trades())

    def generate_performance_report(self, output_file: Optional[str] = None) -> str:
        """Generate quantitative performance report in Markdown."""
        return self.analytics.generate_markdown_report(self.trade_manager.get_closed_trades(), output_file=output_file)


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
                        qty = pos.quantity.as_double() if hasattr(pos, "quantity") and hasattr(pos.quantity, "as_double") else 1.0
                        entry_px = float(pos.avg_px_open) if hasattr(pos, "avg_px_open") and pos.avg_px_open else (pos.avg_px.as_double() if hasattr(pos, "avg_px") and pos.avg_px else 0.0)
                        entry_time = (pos.ts_opened / 1e9) if hasattr(pos, "ts_opened") and pos.ts_opened else None
                        strat_name = str(pos.strategy_id)
                        cur_px = entry_px

                        # 1. Fetch live price and L2 orderbook snapshot
                        bids_depth = 0.0
                        asks_depth = 0.0
                        l2 = self.info_client.get_l2_snapshot(coin)
                        if l2.get("status") == "SUCCESS":
                            bids = l2.get("bids", [])
                            asks = l2.get("asks", [])
                            if bids and asks:
                                cur_px = (float(bids[0]["px"]) + float(asks[0]["px"])) / 2.0
                            elif bids:
                                cur_px = float(bids[0]["px"])
                            elif asks:
                                cur_px = float(asks[0]["px"])

                            bids_depth = sum(float(b["sz"]) * float(b["px"]) for b in bids[:5])
                            asks_depth = sum(float(a["sz"]) * float(a["px"]) for a in asks[:5])

                        # 2. Update TradeManager FIRST so position is tracked with correct entry time and price
                        action = self.trade_manager.update_position(
                            coin=coin,
                            side=side,
                            size=qty,
                            entry_price=entry_px,
                            mark_price=cur_px,
                            strategy=strat_name,
                            entry_time=entry_time,
                        )

                        # 3. Trade Manager risk evaluation (MAE, trailing stop, breakeven, stagnant)
                        if action.should_close:
                            self.log(f"🛡️ [Trade Manager] TRIGGER: {coin} - {action.reason}", "TRIGGER")
                            self.close_position(coin, reason=action.reason)
                            continue

                        # 4. Microstructure depth wall collapse check
                        # ONLY applies to micro-scalps (OrderBookImbalance), NOT to macro swing / carry strategies!
                        # Requires:
                        # - Minimum holding period satisfied (held >= 60s)
                        # - Position is in actual loss (ROI <= -0.8%)
                        # - Severe adverse depth wall (>6.0x skew against position)
                        if "OrderBookImbalance" in strat_name:
                            can_exit, hold_reason = self.trade_manager.check_exit_allowed(coin, is_emergency=False)
                            if can_exit and action.roi <= -0.8:
                                is_short_collapsed = (side == "SHORT" and asks_depth > 0 and (bids_depth / asks_depth) > 6.0)
                                is_long_collapsed = (side == "LONG" and bids_depth > 0 and (asks_depth / bids_depth) > 6.0)
                                if is_short_collapsed or is_long_collapsed:
                                    skew_ratio = (bids_depth / asks_depth) if side == "SHORT" else (asks_depth / bids_depth)
                                    reason = f"Orderbook depth wall collapsed: Skew {skew_ratio:.1f}x > 6.0x (ROI: {action.roi:.2f}%)"
                                    self.log(f"🚨 [AI Sentinel] TRIGGER: {coin} - {reason}", "TRIGGER")
                                    self.close_position(coin, reason=reason)
                                    continue

                        # 5. Adverse funding rate spike (>150% APR)
                        # NEVER trigger on HourlyFundingFade (which intentionally trades high funding rates)
                        # For other strategies, require min holding period satisfied.
                        funding_apr = self.info_client.get_funding_rate(coin)
                        if funding_apr is not None and "FundingFade" not in strat_name:
                            can_exit, _ = self.trade_manager.check_exit_allowed(coin, is_emergency=False)
                            if can_exit:
                                if side == "LONG" and funding_apr > 1.50:
                                    reason = f"Adverse funding spike to +{funding_apr*100:.1f}% APR (toxic long carry)"
                                    self.log(f"🚨 [AI Sentinel] TRIGGER: {coin} - {reason}", "TRIGGER")
                                    self.close_position(coin, reason=reason)
                                    continue
                                if side == "SHORT" and funding_apr < -1.50:
                                    reason = f"Adverse funding drop to {funding_apr*100:.1f}% APR (toxic short carry)"
                                    self.log(f"🚨 [AI Sentinel] TRIGGER: {coin} - {reason}", "TRIGGER")
                                    self.close_position(coin, reason=reason)
                                    continue

                        # Record 10s Sentinel Thought
                        ts_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
                        funding_str = f"{funding_apr*100:+.1f}% APR" if funding_apr is not None else "N/A"
                        skew_ratio = (bids_depth / max(asks_depth, 1.0)) if side == "SHORT" else (asks_depth / max(bids_depth, 1.0))
                        
                        thought_status = "TRIGGER" if action.should_close else ("ALERT" if (action.roi >= 1.0 or action.roi <= -1.5) else "NORMAL")
                        thought_text = f"{coin} {side} [{strat_name}] ({action.roi:+.2f}% ROI | ${action.pnl:+.2f}): L2 Depth Skew {skew_ratio:.1f}x. Funding: {funding_str}. Sentry Action: {action.action} ({action.reason})."

                        thought = {
                            "timestamp": ts_str,
                            "category": "AUDIT",
                            "coin": coin,
                            "side": side,
                            "roi": round(action.roi, 2),
                            "pnl": round(action.pnl, 2),
                            "text": thought_text,
                            "status": thought_status,
                        }
                        self.sentinel_thoughts.append(thought)
                        if len(self.sentinel_thoughts) > 50:
                            self.sentinel_thoughts.pop(0)
                else:
                    # Portfolio is flat
                    ts_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
                    thought = {
                        "timestamp": ts_str,
                        "category": "STANDBY",
                        "coin": "PORTFOLIO",
                        "side": "FLAT",
                        "roi": 0.0,
                        "pnl": 0.0,
                        "text": "Portfolio flat (0 open positions). AI Watchdog in low-power sentry mode. Surveillance active across top 30 perpetuals.",
                        "status": "NORMAL",
                    }
                    self.sentinel_thoughts.append(thought)
                    if len(self.sentinel_thoughts) > 50:
                        self.sentinel_thoughts.pop(0)

                # 1. Cancel stale unmitigated limit orders exceeding timeout
                if self.node and hasattr(self.node, "trader") and self.node.trader and self.guard:
                    stale_orders = self.guard.get_stale_orders_to_cancel()
                    for stale in stale_orders:
                        self.log(f"Cancelling stale order {stale.order_id} on {stale.instrument_id}", "INFO")
                        try:
                            cancel_cmd = CancelOrder(
                                trader_id=self.node.trader_id,
                                strategy_id=StrategyId(stale.strategy_id),
                                instrument_id=stale.instrument_id,
                                client_order_id=ClientOrderId(stale.order_id),
                            )
                            self.node.trader.execute(cancel_cmd)
                        except Exception:
                            pass
                        self.guard.acknowledge_order_cancelled(stale.order_id)

                # 2. Proactive Autonomous Microstructure Scanner (High-Frequency Sentry)
                # Evaluates L2 depth skew on top perpetuals to enter high-conviction orderbook scalps
                if (
                    self.scalp_strat
                    and self.node
                    and hasattr(self.node, "cache")
                    and self.node.cache
                    and len(self.get_open_positions()) < self.guard.max_total_open_positions
                ):
                    scan_coins = self.top_coins[:8] if self.top_coins else list(self.prospects.keys())[:8]
                    for sc_coin in scan_coins:
                        if len(self.get_open_positions()) >= self.guard.max_total_open_positions:
                            break
                        instr_key = f"{sc_coin}-USD-PERP.HYPERLIQUID"
                        instrument = self.scalp_strat.instruments_map.get(instr_key)
                        if not instrument or not self.scalp_strat.portfolio.is_flat(instrument.id):
                            continue
                        if len(self.node.cache.orders_open(instrument_id=instrument.id)) > 0:
                            continue
                        if self.guard.is_in_cooldown(sc_coin):
                            continue

                        l2_snap = self.info_client.get_l2_snapshot(sc_coin)
                        if l2_snap.get("status") == "SUCCESS":
                            bids = l2_snap.get("bids", [])
                            asks = l2_snap.get("asks", [])
                            if bids and asks and float(bids[0]["sz"]) > 0 and float(asks[0]["sz"]) > 0:
                                b_depth = sum(float(b["sz"]) * float(b["px"]) for b in bids[:5])
                                a_depth = sum(float(a["sz"]) * float(a["px"]) for a in asks[:5])
                                if b_depth > 0 and a_depth > 0:
                                    tick_size = instrument.price_increment.as_double()
                                    skew_ratio = b_depth / a_depth
                                    skew_threshold = getattr(self.scalp_strat.scalp_config, "skew_threshold", 1.5)
                                    if skew_ratio >= skew_threshold:
                                        wall_px = float(bids[0]["px"])
                                        entry_px = wall_px
                                        sl_px = wall_px - (self.scalp_strat.scalp_config.stop_ticks * tick_size)
                                        tp_px = entry_px + (self.scalp_strat.scalp_config.take_profit_ticks * tick_size)
                                        self.scalp_strat._execute_scalp(instrument, OrderSide.BUY, entry_px, sl_px, tp_px, skew_ratio)
                                        time.sleep(0.2)
                                    elif (a_depth / b_depth) >= skew_threshold:
                                        skew_rev = a_depth / b_depth
                                        wall_px = float(asks[0]["px"])
                                        entry_px = wall_px
                                        sl_px = wall_px + (self.scalp_strat.scalp_config.stop_ticks * tick_size)
                                        tp_px = entry_px - (self.scalp_strat.scalp_config.take_profit_ticks * tick_size)
                                        self.scalp_strat._execute_scalp(instrument, OrderSide.SELL, entry_px, sl_px, tp_px, skew_rev)
                                        time.sleep(0.2)

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
                    funding = float(ctx.get("funding", 0.0)) * 24 * 365 * 100.0
                    vol_24h = float(ctx.get("dayNtlVlm", 0.0))
                    change_24h = ((px - prev_px) / prev_px * 100) if prev_px > 0 else 0.0
                    records.append({
                        "coin": name, "price": px, "funding_apr": funding,
                        "vol_24h": vol_24h, "change_24h": change_24h,
                    })

                df = pl.DataFrame(records).sort("vol_24h", descending=True).head(50)
                prospects_list = []
                selected_coins = set()

                # 1. Extreme Funding Shorts (Overleveraged Long Fades)
                shorts = df.filter(pl.col("funding_apr") > 50.0).sort("funding_apr", descending=True).head(5)
                for row in shorts.iter_rows(named=True):
                    c = row["coin"]
                    if c not in selected_coins:
                        selected_coins.add(c)
                        px = float(row["price"])
                        funding_apr = float(row["funding_apr"])
                        prospects_list.append({
                            "coin": c,
                            "bias": "SHORT",
                            "conviction_score": min(95, int(80 + (funding_apr / 20.0))),
                            "strategy": "Hourly Funding Fade",
                            "mark_price": px,
                            "target_entry": round(px * 1.008, 4),
                            "stop_loss": round(px * 1.035, 4),
                            "take_profit": round(px * 0.940, 4),
                            "funding_apr_pct": funding_apr,
                            "volume_24h_usd": float(row["vol_24h"]),
                            "rationale": f"Crowded long leverage: Funding APR +{funding_apr:.1f}% on ${row['vol_24h']/1e6:.1f}M 24h vol; longs paying steep hourly fees.",
                        })

                # 2. Spot-Led Long Momentum (Clean Volume with Healthy Baseline Funding)
                longs = df.filter((pl.col("change_24h") > 1.5) & (pl.col("funding_apr") < 35.0)).sort("vol_24h", descending=True).head(5)
                for row in longs.iter_rows(named=True):
                    c = row["coin"]
                    if c not in selected_coins:
                        selected_coins.add(c)
                        px = float(row["price"])
                        funding_apr = float(row["funding_apr"])
                        chg = float(row["change_24h"])
                        prospects_list.append({
                            "coin": c,
                            "bias": "LONG",
                            "conviction_score": min(93, int(82 + chg)),
                            "strategy": "SMC Trend & Spot Divergence",
                            "mark_price": px,
                            "target_entry": round(px * 0.994, 4),
                            "stop_loss": round(px * 0.975, 4),
                            "take_profit": round(px * 1.045, 4),
                            "funding_apr_pct": funding_apr,
                            "volume_24h_usd": float(row["vol_24h"]),
                            "rationale": f"Spot-led accumulation: +{chg:.1f}% 24h on ${row['vol_24h']/1e6:.1f}M vol; healthy baseline funding (+{funding_apr:.1f}% APR).",
                        })

                # 3. Fill remaining slots up to 10 with highest volume leaders
                if len(prospects_list) < 10:
                    for row in df.iter_rows(named=True):
                        c = row["coin"]
                        if c not in selected_coins:
                            selected_coins.add(c)
                            px = float(row["price"])
                            funding_apr = float(row["funding_apr"])
                            chg = float(row["change_24h"])
                            bias = "SHORT" if funding_apr > 40.0 else "LONG"
                            prospects_list.append({
                                "coin": c,
                                "bias": bias,
                                "conviction_score": 80,
                                "strategy": "VWAP / Volume Breakout",
                                "mark_price": px,
                                "target_entry": round(px * (0.995 if bias == "LONG" else 1.005), 4),
                                "stop_loss": round(px * (0.975 if bias == "LONG" else 1.025), 4),
                                "take_profit": round(px * (1.045 if bias == "LONG" else 0.955), 4),
                                "funding_apr_pct": funding_apr,
                                "volume_24h_usd": float(row["vol_24h"]),
                                "rationale": f"Top volume leader (${row['vol_24h']/1e6:.1f}M 24h vol) with {bias} bias and +{funding_apr:.1f}% APR funding.",
                            })
                            if len(prospects_list) >= 10:
                                break

                # Update in-memory prospects map
                self.prospects = {p["coin"]: p for p in prospects_list}
                biases = {p["coin"]: p["bias"] for p in prospects_list}
                self.guard.set_prospect_biases(biases)

                # Feed live funding rates directly into funding strategy actor
                if hasattr(self, "funding_strat") and self.funding_strat:
                    for row in df.iter_rows(named=True):
                        c = row["coin"]
                        apr = float(row["funding_apr"])
                        instr_str = f"{c}-USD-PERP.HYPERLIQUID"
                        self.funding_strat.update_funding_rate(instr_str, apr)

                self.log(f"🎯 [AI Prospector] Updated {len(prospects_list)} high-conviction targets: {[p['coin'] for p in prospects_list]}", "INFO")

                # Sync to bridge/prospects.json
                payload = {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "scan_iteration": getattr(self, "_prospector_scan_count", 1),
                    "macro_sentiment": "NEUTRAL_TO_BULLISH_SELECTIVE_EXPANSION",
                    "prospects": prospects_list,
                }
                with open(self.prospects_path, "w") as f:
                    json.dump(payload, f, indent=2)

                # 3. Scan Delta-Neutral Funding Carry Arbitrage opportunities
                try:
                    arb_pairs = self.arbitrage_scanner.scan_and_save()
                    self.funding_arbitrage_pairs = arb_pairs
                    if arb_pairs:
                        top_pair = arb_pairs[0]
                        self.log(
                            f"💎 [Funding Arbitrage] Detected {len(arb_pairs)} delta-neutral carry pairs. "
                            f"Top: {top_pair['pair_id']} @ +{top_pair['net_carry_apr_pct']}% Net Carry APR",
                            "TRIGGER",
                        )
                    else:
                        self.log("💎 [Funding Arbitrage] Scan complete: 0 pairs qualified (strict < -50% & > +100% APR with spread <= 0.10% & depth >= $10k)", "INFO")
                except Exception as arb_err:
                    self.log(f"Funding arbitrage scan warning: {arb_err}", "WARN")

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
