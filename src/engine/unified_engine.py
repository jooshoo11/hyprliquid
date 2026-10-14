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
import queue
import threading
import signal
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone, timedelta

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
from decimal import Decimal
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OrderSide, OrderType, TimeInForce, TriggerType
from nautilus_trader.model.objects import MarginBalance, Money, AccountBalance
from nautilus_trader.model.events.account import AccountState
from nautilus_trader.core.uuid import UUID4

from src.strategies.continuation import TrendContinuationSMC, TrendContinuationConfig
from src.strategies.funding_fade import HourlyFundingFade, HourlyFundingFadeConfig
from src.strategies.orderbook_scalp import OrderBookImbalance, OrderBookImbalanceConfig
from src.strategies.vwap_momentum import VwapOiMomentum, VwapOiMomentumConfig
from src.strategies.liquidation_absorber import LiquidationAbsorber, LiquidationAbsorberConfig
from src.strategies.high_conviction_momentum import HighConvictionMomentumStrategy, HighConvictionMomentumConfig
from src.risk.portfolio_guard import PortfolioGuard
from src.risk.trade_manager import TradeManager, TradeAction
from src.risk.performance_analytics import PerformanceAnalytics
from src.risk.fallback_engine import AIFallbackEngine
from src.risk.regime_manager import MarketRegimeManager, MarketRegimeInfo
from src.scanner.mcp_client import HyperliquidInfoClient
from src.scanner.arbitrage_scanner import ArbitrageScanner
from src.scanner.analytics_pipeline import AnalyticsPipeline
from src.utils.instruments import get_coin_max_leverage, to_hl_price
from src.execution.node_runner import generate_or_load_wallet
from src.execution.agent_wallet import HyperliquidAgentWallet

console = Console()


class UnifiedEngine:
    """
    Master Autonomous Engine coordinating TradingNode, AI Sentinel, and Web API.
    """

    def __init__(
        self,
        top_n: int = 50,
        paper: bool = True,
        risk_pct: float = 0.01,
        rr_ratio: float = 2.5,
        web_port: int = 8000,
        watchdog_interval: float = 10.0,
        prospector_interval: float = 900.0,
    ):
        self.top_n = top_n
        self.paper = paper
        self.risk_pct = risk_pct
        self.rr_ratio = rr_ratio
        self.web_port = web_port
        self.watchdog_interval = float(watchdog_interval)
        self.prospector_interval = float(prospector_interval)
        self._last_deep_enrichment_ts: float = 0.0
        self._last_arb_scan_ts: float = 0.0
        self._cancelled_orphan_ids: set[str] = set()

        self.info_client = HyperliquidInfoClient(network="mainnet" if paper else "testnet")
        self.wallet_address, self._private_key, self.is_ephemeral = generate_or_load_wallet()
        self.agent_wallet = HyperliquidAgentWallet(
            main_address=self.wallet_address,
            network="mainnet" if self.paper else "testnet",
        )

        self.guard = PortfolioGuard(
            max_strategy_equity_pct=1.00,
            max_total_open_positions=2,
            max_daily_drawdown_pct=0.20,
            max_single_position_equity_pct=None,
            allow_pyramiding=False,
            bypass_risk_guard=False,
            allow_reversals=True,
        )
        self.guard.reentry_cooldown_seconds = 900.0  # 15 minutes global re-entry cooldown
        self.trade_manager = TradeManager(
            min_holding_seconds=60.0,        # 60s minimum hold to prevent instant noise whipsaws
            reentry_cooldown_seconds=1800.0,  # 30 minutes strict post-trade coin lockout
            take_profit_roi_pct=2.80,        # Bank +2.8% price move (healthy institutional swing target)
            breakeven_roi_pct=1.20,          # Move stop to breakeven once price is up +1.20%
            breakeven_stop_roi_pct=0.25,     # +0.25% stop locks in profit comfortably above fees
            trailing_roi_pct=1.80,           # Activate trailing stop once in solid profit at +1.80%
            trailing_distance_pct=0.85,      # Healthy 0.85% trail behind peak price
            mae_roi_pct=-1.50,               # Hard cut at -1.50% price move
            mae_loss_usd=-5.00,              # Hard dollar stop per position
            fast_dud_minutes=25.0,           # Exit trades that chop flat for 25 minutes
            fast_dud_roi_pct=-0.50,
            fast_dud_max_peak_roi=0.25,
            stagnant_hours=2.0,              # 2 hours maximum total duration per trade
            tiered_profit_lock=True,
            enable_partial_tp=True,          # Master Quant Alpha: 50% TP1 scale-out
            max_positions=2,
        )
        # Max share of equity that may be lost if a trade hits its stop (6% = $12 on $200). Drives notional clamp.
        self.max_risk_pct_per_trade: float = 0.06
        # coin -> {"ts": placed_time, "orders": [(strategy_actor, order), ...]} for exchange-resident reduce-only protection
        self._protection_orders: Dict[str, Dict[str, Any]] = {}
        self.analytics = PerformanceAnalytics()
        self.fallback_engine = AIFallbackEngine()
        self.regime_manager = MarketRegimeManager()

        self.node: Optional[TradingNode] = None
        self.momentum_strat: Optional[HighConvictionMomentumStrategy] = None
        self.continuation_strat: Optional[TrendContinuationSMC] = None
        self.funding_strat: Optional[HourlyFundingFade] = None
        self.scalp_strat: Optional[OrderBookImbalance] = None
        self.vwap_strat: Optional[VwapOiMomentum] = None
        self.absorber_strat: Optional[LiquidationAbsorber] = None

        self.top_coins: List[str] = []
        self.prospects: Dict[str, Any] = {}
        self.funding_arbitrage_pairs: List[Dict[str, Any]] = []
        self.audit_events: List[Dict[str, Any]] = []
        self.sentinel_thoughts: List[Dict[str, Any]] = []

        self.arbitrage_scanner = ArbitrageScanner(info_client=self.info_client)
        self.analytics_pipeline = AnalyticsPipeline(dashboard_port=8001, auto_start=True)

        self._is_running = False
        self._threads: List[threading.Thread] = []

        # State persistence paths
        self.paper_state_path = os.path.join(REPO_ROOT, "bridge", "paper_state.json")
        self.active_trades_path = os.path.join(REPO_ROOT, "bridge", "active_trades.json")
        self.prospects_path = os.path.join(REPO_ROOT, "bridge", "prospects.json")
        self.funding_arbitrage_path = os.path.join(REPO_ROOT, "bridge", "funding_arbitrage.json")
        self.market_regime_path = os.path.join(REPO_ROOT, "bridge", "market_regime.json")
        self.ai_commands_path = os.path.join(REPO_ROOT, "bridge", "ai_commands.json")
        self.ai_status_path = os.path.join(REPO_ROOT, "bridge", "ai_status.json")
        self._paper_starting_balance = 200.0
        self._paper_starting_equity = 200.0
        self._paper_realized_pnl = 0.0
        self._cached_px_map: Dict[str, float] = {}
        self._last_px_map_time: float = 0.0
        self.coin_atr_pct: Dict[str, float] = {}
        self._prospector_scan_count = 0
        self._last_prospector_scan = 0.0
        self._next_prospector_scan = 0.0
        self._last_watchdog_audit = 0.0
        self._last_proactive_scalp_scan = 0.0
        self._force_prospector_scan = False
        self._in_flight_closes: Dict[str, float] = {}  # coin -> timestamp when close was submitted
        self._closed_positions_coins: set[str] = set()  # set of coins whose close was submitted in this session
        self._benchmark_impulse_cache: Tuple[float, float] = (0.0, 0.0)
        self._benchmark_impulse_cache_ts: float = 0.0
        self._coin_kline_cache: Dict[str, Tuple[float, Any]] = {}  # coin -> (timestamp, metrics_dict)
        self.command_queue: queue.Queue = queue.Queue()
        self._latest_state: Dict[str, Any] = {}
        self._lock: threading.RLock = threading.RLock()

    def submit_command(self, cmd: Dict[str, Any]) -> None:
        """Push a command to the thread-safe in-memory command queue for low-latency dispatch."""
        self.command_queue.put(cmd)

    def _update_ai_status(self, open_count: int = 0) -> None:
        """Update bridge/ai_status.json with live watchdog and prospector cadence."""
        try:
            fb = self.fallback_engine.get_status_summary() if hasattr(self, "fallback_engine") else {}
            status_payload = {
                "status": fb.get("status", "HEALTHY"),
                "is_fallback_active": fb.get("is_fallback_active", False),
                "last_error": fb.get("last_error"),
                "fallback_count": fb.get("fallback_count", 0),
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "prospector": {
                    "status": "ACTIVE",
                    "interval_seconds": self.prospector_interval,
                    "interval_minutes": int(self.prospector_interval / 60),
                    "scan_count": self._prospector_scan_count,
                    "last_scan": datetime.fromtimestamp(self._last_prospector_scan, timezone.utc).isoformat() if self._last_prospector_scan else None,
                    "next_scan": datetime.fromtimestamp(self._next_prospector_scan, timezone.utc).isoformat() if self._next_prospector_scan else None,
                },
                "watchdog": {
                    "status": "ACTIVE",
                    "interval_seconds": self.watchdog_interval,
                    "target": "ACTIVE_TRADES",
                    "active_positions_monitored": open_count,
                    "last_audit": datetime.fromtimestamp(self._last_watchdog_audit, timezone.utc).isoformat() if self._last_watchdog_audit else None,
                },
            }
            tmp_status = f"{self.ai_status_path}.tmp"
            with open(tmp_status, "w") as f:
                json.dump(status_payload, f, indent=2)
            os.replace(tmp_status, self.ai_status_path)
        except Exception as e:
            self.log(f"Failed to update AI status: {e}", "DEBUG")

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
                self._paper_starting_balance = float(state.get("starting_balance", 200.0))
                self._paper_realized_pnl = float(state.get("realized_pnl", 0.0))
                restored_cash = self._paper_starting_balance + self._paper_realized_pnl
                self._paper_starting_equity = float(state.get("equity", restored_cash))
                self.log(
                    f"Restored paper state: Equity=${self._paper_starting_equity:.2f} | "
                    f"Starting Balance=${self._paper_starting_balance:.2f} | "
                    f"Realized P&L=${self._paper_realized_pnl:+.2f}"
                )
        except Exception as e:
            self.log(f"Paper state init fresh at $200 ({e})", "WARN")

    def save_paper_state(self, equity: float, realized_pnl: float, positions: List[Dict]) -> None:
        """Atomically persist paper equity to disk."""
        if not self.paper:
            return
        try:
            total_closed = len(self.trade_manager.get_closed_trades()) if hasattr(self, "trade_manager") and self.trade_manager else 0
            state = {
                "equity": round(equity, 2),
                "realized_pnl": round(realized_pnl, 2),
                "open_positions": positions,
                "session_start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "total_trades": total_closed,
                "starting_balance": round(self._paper_starting_balance, 2),
            }
            tmp = f"{self.paper_state_path}.tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.paper_state_path)
        except Exception as e:
            self.log(f"Failed to save paper state: {e}", "DEBUG")

    def setup(self) -> None:
        """Initialize Nautilus TradingNode and Strategy Actors."""
        self.load_paper_state()
        self.guard.update_dynamic_allocations(self.trade_manager.get_closed_trades())
        mode_str = "MAINNET (LOCAL EMULATOR)" if self.paper else "TESTNET (BURNER KEY)"
        self.log(f"🚀 Initializing Unified Autonomous Trading Engine - {mode_str}...")

        # Screen top perpetuals
        top_markets = self.info_client.get_top_perpetuals(top_n=self.top_n)
        self.top_coins = [m["name"] for m in top_markets]
        for benchmark in ["BTC", "ETH", "SOL"]:
            if benchmark not in self.top_coins:
                self.top_coins.append(benchmark)
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
            restored_cash = self._paper_starting_balance + self._paper_realized_pnl
            exec_cfg = SandboxExecutionClientConfig(
                venue="HYPERLIQUID",
                starting_balances=[f"{restored_cash} USD"],
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
            restored_cash = self._paper_starting_balance + self._paper_realized_pnl
            mock_state = AccountState(
                AccountId("HYPERLIQUID-001"),
                AccountType.MARGIN,
                USD,
                False,
                [AccountBalance(Money(restored_cash, USD), Money(0.0, USD), Money(restored_cash, USD))],
                [MarginBalance(Money(restored_cash, USD), Money(restored_cash, USD))],
                {},
                UUID4(),
                int(time.time() * 10**9),
                int(time.time() * 10**9),
            )
            self.node.portfolio.update_account(mock_state)

        # Load tuned hyperparameter configurations if present
        opt_params = {}
        opt_path = os.path.join(REPO_ROOT, "config", "optimized_params.json")
        if os.path.exists(opt_path):
            try:
                with open(opt_path, "r") as f:
                    payload = json.load(f)
                    opt_params = payload.get("parameters", {})
            except Exception:
                opt_params = {}

        # Instantiate Strategy Actors
        self.momentum_strat = HighConvictionMomentumStrategy(
            config=HighConvictionMomentumConfig(venue="HYPERLIQUID", risk_per_trade_pct=self.risk_pct),
            portfolio_guard=self.guard,
        )
        self.continuation_strat = TrendContinuationSMC(
            config=TrendContinuationConfig(
                venue="HYPERLIQUID",
                risk_per_trade_pct=opt_params.get("continuation_risk_pct", self.risk_pct),
                reward_to_risk_ratio=opt_params.get("continuation_rr_ratio", self.rr_ratio),
                ema_fast_period=opt_params.get("continuation_ema_fast", 9),
                ema_slow_period=opt_params.get("continuation_ema_slow", 21),
                displacement_multiplier=opt_params.get("continuation_displacement", opt_params.get("continuation_displacement_multiplier", 1.5)),
            ),
            portfolio_guard=self.guard,
        )
        self.funding_strat = HourlyFundingFade(
            config=HourlyFundingFadeConfig(
                venue="HYPERLIQUID",
                risk_per_trade_pct=opt_params.get("funding_risk_pct", opt_params.get("funding_fade_risk_pct", min(self.risk_pct, 0.0075))),
                min_funding_apr_threshold=opt_params.get("funding_apr_threshold", opt_params.get("min_funding_apr_threshold", 0.30)),
                trailing_stop_pct=opt_params.get("funding_trailing_stop", opt_params.get("funding_fade_trailing_stop", 0.015)),
            ),
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
        self.absorber_strat = LiquidationAbsorber(
            config=LiquidationAbsorberConfig(venue="HYPERLIQUID", post_only=True),
            portfolio_guard=self.guard,
        )

        # Register all active strategies with TradingNode trader actor
        active_strats = [
            self.momentum_strat,
            self.continuation_strat,
            self.funding_strat,
            self.scalp_strat,
            self.vwap_strat,
            self.absorber_strat,
        ]
        registered_ids = set()
        if hasattr(self.node, "trader") and self.node.trader:
            if hasattr(self.node.trader, "strategies"):
                strats = self.node.trader.strategies
                if callable(strats):
                    strats = strats()
                if isinstance(strats, dict):
                    registered_ids = set(strats.keys())
                elif isinstance(strats, (list, tuple, set)):
                    registered_ids = {getattr(s, "id", s) for s in strats}
            for strat in active_strats:
                if strat is not None:
                    strat_id = getattr(strat, "id", None)
                    if strat_id is None or strat_id not in registered_ids:
                        try:
                            self.node.trader.add_strategy(strat)
                            if strat_id:
                                registered_ids.add(strat_id)
                        except Exception as reg_err:
                            self.log(f"Notice registering strategy {strat}: {reg_err}", "DEBUG")

        self.log("🚀 Active multi-strategy portfolio actors (Momentum, SMC Continuation, Funding Fade, Scalper, VWAP, Absorber) registered under PortfolioGuard.", "TRIGGER")

    def get_open_positions(self) -> List[Any]:
        """Fetch active open positions directly from Nautilus cache, filtering out closed/in-flight positions."""
        try:
            if self.node and self.node.cache:
                raw_positions = [p for p in self.node.cache.positions_open() if not p.is_closed]
                filtered = []
                recently_closed = getattr(self.trade_manager, "_recently_closed_times", {}) if hasattr(self, "trade_manager") and self.trade_manager else {}
                for p in raw_positions:
                    c = p.instrument_id.symbol.value.split("-")[0].upper()
                    if c in self._in_flight_closes:
                        continue
                    if c in self._closed_positions_coins:
                        continue
                    if c in recently_closed:
                        continue
                    # Filter residual dust positions (< $5.0 USD notional) so rounding dust does not block trade slots
                    qty_val = abs(p.quantity.as_double()) if hasattr(p, "quantity") and hasattr(p.quantity, "as_double") else abs(float(getattr(p, "size", 0.0)))
                    px_val = float(p.avg_px_open) if hasattr(p, "avg_px_open") and p.avg_px_open else (float(getattr(p, "avg_px", 0.0)))
                    if px_val > 0 and (qty_val * px_val) < 5.0:
                        continue
                    filtered.append(p)
                return filtered
        except Exception as err:
            self.log(f"Error fetching open positions from cache: {err}", "DEBUG")
        return []

    def get_5m_rsi(self, coin: str, period: int = 14) -> Optional[float]:
        """Fetch recent 5m klines and compute 14-period RSI to detect oversold/overbought exhaustion."""
        try:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - (period + 12) * 5 * 60 * 1000
            klines = self.info_client.get_historical_klines(coin, "5m", start_time_ms=start_ms, end_time_ms=now_ms)
            if not klines or len(klines) < period + 1:
                return None
            closes = [float(k["c"]) for k in klines]
            diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
            gains = [d if d > 0 else 0.0 for d in diffs]
            losses = [-d if d < 0 else 0.0 for d in diffs]
            avg_gain = sum(gains[:period]) / period
            avg_loss = sum(losses[:period]) / period
            for i in range(period, len(diffs)):
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return 100.0 - (100.0 / (1.0 + rs))
        except Exception as err:
            self.log(f"Error calculating 5m RSI for {coin}: {err}", "DEBUG")
            return None

    def _get_net_realized_pnl(self) -> float:
        """Fetch cumulative net realized PnL from TradeManager as canonical source of truth."""
        if hasattr(self, "trade_manager") and self.trade_manager:
            try:
                tm_summary = self.trade_manager.get_summary()
                if "net_realized_pnl" in tm_summary:
                    return float(tm_summary["net_realized_pnl"])
                closed = self.trade_manager.get_closed_trades()
                if closed:
                    return round(sum(float(t.get("net_pnl", t.get("pnl", 0.0))) for t in closed), 2)
            except Exception as e:
                self.log(f"Error computing net realized pnl: {e}", "DEBUG")
        return self._paper_realized_pnl

    def get_account_cash(self) -> float:
        """Fetch current cash balance from portfolio or paper balance tracker."""
        if self.paper:
            self._paper_realized_pnl = self._get_net_realized_pnl()
            return self._paper_starting_balance + self._paper_realized_pnl
        try:
            if self.node and self.node.portfolio:
                venue = Venue("HYPERLIQUID")
                acct = self.node.portfolio.account(venue=venue)
                if acct:
                    bal = acct.balance_total(USD)
                    if bal:
                        return bal.as_double()
        except Exception as err:
            self.log(f"Error fetching account cash from portfolio: {err}", "DEBUG")
        return self._paper_starting_balance + self._paper_realized_pnl

    def get_state(self) -> Dict[str, Any]:
        """Assemble live portfolio, trade, and AI state directly from in-memory engine."""
        cash_balance = self.get_account_cash()
        open_positions = self.get_open_positions()

        # Fetch live prices with 5-second TTL caching to prevent 429 HTTP rate limits
        now_ts = time.time()
        if not self._cached_px_map or (now_ts - self._last_px_map_time) > 5.0:
            try:
                meta, asset_ctxs = self.info_client.get_meta_and_asset_ctxs()
                universe = meta.get("universe", [])
                new_map = {}
                for u, ctx in zip(universe, asset_ctxs):
                    new_map[u.get("name")] = float(ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx", 0.0))
                self._cached_px_map = new_map
                self._last_px_map_time = now_ts
            except Exception as err:
                self.log(f"Error fetching live prices in get_state: {err}", "DEBUG")
        px_map = self._cached_px_map

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
                if "HighConviction" in strat_raw:
                    strat_name = "High-Conviction Momentum"
                elif "Funding" in strat_raw:
                    strat_name = "Funding Fade"
                elif "OrderBook" in strat_raw or "Scalp" in strat_raw or "Imbalance" in strat_raw:
                    strat_name = "Book Imbalance"
                elif "Vwap" in strat_raw:
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
                    "leverage": float(get_coin_max_leverage(coin)),
                })
            except Exception as pos_err:
                self.log(f"Error parsing position item in get_state: {pos_err}", "DEBUG")

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
            except Exception as prosp_err:
                self.log(f"Error loading prospects in get_state: {prosp_err}", "DEBUG")

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
            except Exception as arb_err:
                self.log(f"Error loading funding arbitrage in get_state: {arb_err}", "DEBUG")

        open_orders_data = []
        recent_orders_data = []
        try:
            if self.node and self.node.cache:
                for o in self.node.cache.orders_open():
                    open_orders_data.append({
                        "order_id": str(o.client_order_id),
                        "instrument_id": str(o.instrument_id),
                        "side": o.side.name,
                        "type": o.order_type.name,
                        "quantity": float(o.quantity.as_double()) if hasattr(o.quantity, "as_double") else float(o.quantity),
                        "price": float(o.price.as_double()) if hasattr(o, "price") and o.price else 0.0,
                    })
                for o in self.node.cache.orders()[-10:]:
                    reason = ""
                    for ev in getattr(o, "events", []):
                        if hasattr(ev, "reason"):
                            reason = str(ev.reason)
                    recent_orders_data.append({
                        "order_id": str(o.client_order_id),
                        "instrument_id": str(o.instrument_id),
                        "side": o.side.name,
                        "type": o.order_type.name,
                        "status": o.status.name,
                        "reason": reason,
                        "quantity": float(o.quantity.as_double()) if hasattr(o.quantity, "as_double") else float(o.quantity),
                    })
        except Exception as ord_err:
            self.log(f"Error fetching orders from cache in get_state: {ord_err}", "DEBUG")

        return {
            "timestamp": time.time(),
            "equity": total_equity,
            "cash_balance": round(cash_balance, 2),
            "net_unrealized": round(total_unrealized, 2),
            "notional_exposure": round(total_notional, 2),
            "roi_pct": roi_total,
            "net_realized_pnl": round(self._get_net_realized_pnl(), 2),
            "positions": positions_data,
            "open_orders": open_orders_data,
            "recent_orders": recent_orders_data,
            "prospects": prospects_list,
            "funding_arbitrage": funding_arb_pairs,
            "sentinel_thoughts": self.sentinel_thoughts[-25:],
            "audit_events": self.audit_events[-20:],
            "circuit_breaker": "TRIPPED" if self.guard.is_circuit_breaker_triggered else "NORMAL",
            "risk_guard": "ARMED",
            "leverage_mode": "EXCHANGE_MAX",
            "paper": self.paper,
            "trade_manager": self.trade_manager.get_summary(),
            "strategy_allocations": self.guard.get_strategy_performance_status() if hasattr(self, "guard") and self.guard else {},
            "performance_metrics": self.analytics.get_metrics(self.trade_manager.get_closed_trades()),
            "prospector_interval": self.prospector_interval,
            "watchdog_interval": self.watchdog_interval,
            "next_prospector_scan": datetime.fromtimestamp(self._next_prospector_scan, timezone.utc).isoformat() if self._next_prospector_scan else None,
            "last_watchdog_audit": datetime.fromtimestamp(self._last_watchdog_audit, timezone.utc).isoformat() if self._last_watchdog_audit else None,
            "fallback_status": self.fallback_engine.get_status_summary() if hasattr(self, "fallback_engine") else {},
            "market_regime": self.regime_manager.current_regime.to_dict() if hasattr(self, "regime_manager") and self.regime_manager and self.regime_manager.current_regime else None,
            "agent_wallet": self.agent_wallet.get_status() if hasattr(self, "agent_wallet") and self.agent_wallet else {},
            "analytics_dashboard_online": self.analytics_pipeline.is_dashboard_online() if hasattr(self, "analytics_pipeline") else False,
        }

    def close_position(self, coin: str, reason: str = "Manual Close") -> bool:
        """Natively and immediately close an open position across any strategy."""
        with self._lock:
            coin_clean = coin.split("-")[0].split(".")[0].strip().upper()

            if not self.node or not self.node.cache:
                return False

            # Guard against in-flight close order re-entrancy (e.g. order pending in emulator / watchdog multi-calling)
            now_ts = time.time()
            in_flight_time = self._in_flight_closes.get(coin_clean, 0.0)
            if (now_ts - in_flight_time) < 600.0:
                self.log(f"Close already in-flight for {coin_clean} ({int(now_ts - in_flight_time)}s ago), suppressing duplicate close command.", "DEBUG")
                return False

            open_positions = [
                p for p in self.node.cache.positions_open()
                if not p.is_closed and (
                    p.instrument_id.symbol.value.split("-")[0].upper() == coin_clean
                )
            ]
            if not open_positions:
                self.log(f"No active position found to close for {coin_clean}", "WARN")
                return False

            closed_any = False
            for pos in open_positions:
                actual_coin = pos.instrument_id.symbol.value.split("-")[0].upper()

                # BEFORE submitting strat.close_position(pos) or any market close order:
                # Inspect self.node.cache.orders_open(instrument_id=pos.instrument_id).
                # Immediately iterate and call strat.cancel_order(o) across all strategy actors.
                # Log each cancellation.
                all_strats = [s for s in [self.momentum_strat, self.continuation_strat, self.funding_strat, self.scalp_strat, self.vwap_strat, self.absorber_strat] if s is not None]
                try:
                    if self.node and self.node.cache:
                        for o in list(self.node.cache.orders_open(instrument_id=pos.instrument_id)):
                            for strat in all_strats:
                                try:
                                    strat.cancel_order(o)
                                except Exception as cancel_err:
                                    self.log(f"Failed to cancel order {getattr(o, 'client_order_id', o)} via {getattr(strat, 'id', strat)}: {cancel_err}", "DEBUG")
                            self.log(f"Pre-close atomically cancelled resting order {o.client_order_id} on {actual_coin}", "INFO")
                except Exception as err:
                    self.log(f"Pre-close order sweep error on {actual_coin}: {err}", "WARN")

                # THEN submit the market close (strat.close_position(pos))
                for strat in all_strats:
                    if strat.id == pos.strategy_id:
                        try:
                            strat.close_position(pos)
                            closed_any = True
                            self.log(f"Market close submitted for {pos.instrument_id.symbol.value} via {strat.id} ({reason})", "CLOSE")
                            break
                        except Exception as close_err:
                            self.log(f"close_position on {strat.id} notice: {close_err}", "DEBUG")

                if not closed_any:
                    active_trader_strats = list(getattr(self.node.trader, "_strategies", {}).values()) if (self.node and self.node.trader) else []
                    for fallback in (active_trader_strats or all_strats):
                        try:
                            fallback.close_position(pos)
                            closed_any = True
                            self.log(f"Market close submitted for {pos.instrument_id.symbol.value} via fallback {fallback.id} ({reason})", "CLOSE")
                            break
                        except Exception as fb_err:
                            self.log(f"Fallback close via {getattr(fallback, 'id', fallback)}: {fb_err}", "DEBUG")

                if not closed_any and self.node and self.node.trader:
                    active_trader_strats = list(getattr(self.node.trader, "_strategies", {}).values())
                    for active_strat in active_trader_strats:
                        try:
                            order_side = OrderSide.SELL if pos.is_long else OrderSide.BUY
                            market_order = active_strat.order_factory.market(
                                instrument_id=pos.instrument_id,
                                order_side=order_side,
                                quantity=pos.quantity,
                            )
                            active_strat.submit_order(market_order)
                            closed_any = True
                            self.log(f"Market close executed for {actual_coin} via opposing order on {active_strat.id} ({reason})", "CLOSE")
                            break
                        except Exception as opp_err:
                            self.log(f"Opposing market order error via {active_strat.id}: {opp_err}", "DEBUG")

                # Live execution fallback via HyperliquidAgentWallet
                if not closed_any and not self.paper and hasattr(self, "agent_wallet") and self.agent_wallet.is_configured:
                    qty = pos.quantity.as_double() if hasattr(pos, "quantity") and hasattr(pos.quantity, "as_double") else 1.0
                    res = self.agent_wallet.place_order(
                        coin=actual_coin,
                        is_buy=not pos.is_long,
                        size=qty,
                        limit_px=float(pos.avg_px_open) if hasattr(pos, "avg_px_open") and pos.avg_px_open else 0.0,
                        order_type={"market": {}},
                        reduce_only=True,
                    )
                    if res:
                        closed_any = True
                        self.log(f"Market close submitted for {actual_coin} via native AgentWallet ({reason})", "CLOSE")

                if closed_any:
                    self._cancel_exchange_protection(actual_coin)
                    self._in_flight_closes[actual_coin] = now_ts
                    self._in_flight_closes[coin_clean] = now_ts
                    self._closed_positions_coins.add(actual_coin)
                    self._closed_positions_coins.add(coin_clean)

                    entry_px = float(pos.avg_px_open) if hasattr(pos, "avg_px_open") and pos.avg_px_open else (pos.avg_px.as_double() if hasattr(pos, "avg_px") and pos.avg_px else 0.0)
                    qty = pos.quantity.as_double() if hasattr(pos, "quantity") and hasattr(pos.quantity, "as_double") else 1.0
                    side = "LONG" if pos.is_long else "SHORT"
                    strat_id = str(pos.strategy_id)
                    exit_px = entry_px
                    tracker = self.trade_manager.get_position(actual_coin)
                    if tracker and tracker.current_price > 0:
                        exit_px = tracker.current_price

                    self.trade_manager.close_and_journal_position(
                        coin=actual_coin,
                        exit_price=exit_px,
                        reason=reason,
                        fallback_side=side,
                        fallback_size=qty,
                        fallback_entry_price=entry_px,
                        fallback_strategy=strat_id,
                    )

                    # Sync realized P&L and available cash immediately
                    self._paper_realized_pnl = self._get_net_realized_pnl()
                    total_equity = self._paper_starting_balance + self._paper_realized_pnl
                    self.save_paper_state(
                        equity=total_equity,
                        realized_pnl=self._paper_realized_pnl,
                        positions=[p for p in self.get_open_positions() if p.instrument_id != pos.instrument_id],
                    )

                    # Set anti-churn cooldown (180s on MAE cut, 120s on normal scalp exit)
                    cooldown_dur = 180.0 if "MAE" in str(reason) else 120.0
                    if hasattr(self, "guard") and self.guard:
                        self.guard.set_cooldown(actual_coin, cooldown_dur)
                        self.guard.register_position_closed(
                            strategy_name=strat_id,
                            instrument_id=pos.instrument_id,
                            freed_notional_usd=abs(qty * entry_px),
                        )
                        self.guard.sync_open_positions(self.get_open_positions())
                    if hasattr(self, "portfolio_guard") and self.portfolio_guard:
                        self.portfolio_guard.set_cooldown(actual_coin, cooldown_dur)
                    if hasattr(self, "trade_manager") and self.trade_manager:
                        self.trade_manager.set_cooldown(actual_coin)

                    # Dynamically adjust risk allocations based on updated trade performance
                    if hasattr(self, "guard") and self.guard:
                        self.guard.update_dynamic_allocations(self.trade_manager.get_closed_trades())

            return closed_any

    def _place_exchange_protection(
        self,
        strat_actor: Any,
        instrument: Any,
        bias: str,
        quantity: Any,
        sl_px: float,
        tp_px: float,
    ) -> None:
        """
        LIVE ONLY. Rest a reduce-only stop-market (SL) and reduce-only limit (TP) on the exchange so protection
        survives a process crash / lost connection. Software ratchets (breakeven / trailing) still run in
        TradeManager and will usually exit first; these orders are the hard backstop at the displayed SL / TP.
        """
        if self.paper or strat_actor is None:
            return
        coin = instrument.id.symbol.value.split("-")[0].upper()
        self._cancel_exchange_protection(coin)
        close_side = OrderSide.SELL if str(bias).upper() == "LONG" else OrderSide.BUY
        placed: list = []
        try:
            if sl_px and sl_px > 0:
                sl_order = strat_actor.order_factory.stop_market(
                    instrument_id=instrument.id,
                    order_side=close_side,
                    quantity=quantity,
                    trigger_price=instrument.make_price(Decimal(str(to_hl_price(sl_px, instrument.price_precision)))),
                    trigger_type=TriggerType.LAST_PRICE,
                    reduce_only=True,
                )
                strat_actor.submit_order(sl_order)
                placed.append((strat_actor, sl_order))
            if tp_px and tp_px > 0:
                tp_order = strat_actor.order_factory.limit(
                    instrument_id=instrument.id,
                    order_side=close_side,
                    quantity=quantity,
                    price=instrument.make_price(Decimal(str(to_hl_price(tp_px, instrument.price_precision)))),
                    time_in_force=TimeInForce.GTC,
                    reduce_only=True,
                )
                strat_actor.submit_order(tp_order)
                placed.append((strat_actor, tp_order))
        except Exception as prot_err:
            self.log(f"⚠️ [Exchange Protection] Failed to place SL/TP for {coin}: {prot_err}", "ERROR")
        if placed:
            self._protection_orders[coin] = {"ts": time.time(), "orders": placed}
            self.log(f"🛡️ [Exchange Protection] {coin}: resting reduce-only SL @ {sl_px} and TP @ {tp_px} on exchange", "INFO")

    def _cancel_exchange_protection(self, coin: str) -> None:
        """Cancel any resting protection orders for a coin (idempotent, safe in paper mode)."""
        entry = self._protection_orders.pop(str(coin).upper(), None)
        if not entry:
            return
        for actor, order in entry.get("orders", []):
            try:
                if not order.is_closed:
                    actor.cancel_order(order)
            except Exception as cancel_err:
                self.log(f"Protection order cancel notice for {coin}: {cancel_err}", "DEBUG")

    def _sweep_protection_orders(self) -> None:
        """Cancel protection orders whose position is gone (e.g. exchange SL filled, leaving the TP leg resting)."""
        if self.paper or not self._protection_orders:
            return
        open_coins = {p.instrument_id.symbol.value.split("-")[0].upper() for p in self.get_open_positions()}
        now = time.time()
        for coin in list(self._protection_orders.keys()):
            if coin not in open_coins and (now - self._protection_orders[coin].get("ts", 0.0)) > 15.0:
                self.log(f"🧹 [Exchange Protection] {coin} flat; cancelling leftover protection orders", "INFO")
                self._cancel_exchange_protection(coin)

    def partial_close_position(self, coin: str, pct: float = 0.50, reason: str = "TP1 Scale-Out") -> bool:
        """Natively and immediately execute a partial market close (e.g. 50% TP1 scale-out)."""
        with self._lock:
            coin_clean = coin.split("-")[0].split(".")[0].strip().upper()
            if not self.node or not self.node.cache:
                return False

            open_positions = [
                p for p in self.node.cache.positions_open()
                if not p.is_closed and (
                    p.instrument_id.symbol.value.split("-")[0].upper() == coin_clean
                )
            ]
            if not open_positions:
                self.log(f"No active position found to partial close for {coin_clean}", "WARN")
                return False

            partial_done = False
            for pos in open_positions:
                actual_coin = pos.instrument_id.symbol.value.split("-")[0].upper()
                instrument = self.node.cache.instrument(pos.instrument_id)
                if not instrument:
                    continue

                full_qty = pos.quantity.as_double() if hasattr(pos.quantity, "as_double") else float(pos.quantity)
                raw_close_qty = full_qty * pct

                # Lot-step truncation
                step = Decimal(str(instrument.size_increment.as_double()))
                close_qty_dec = (Decimal(str(round(raw_close_qty, instrument.size_precision))) // step) * step
                if close_qty_dec <= 0:
                    self.log(f"Partial close quantity too small for {actual_coin} ({raw_close_qty} -> 0)", "WARN")
                    continue

                close_qty = instrument.make_qty(close_qty_dec)
                order_side = OrderSide.SELL if pos.is_long else OrderSide.BUY

                # Find strategy actor to submit opposing market order
                all_strats = [s for s in [self.momentum_strat, self.continuation_strat, self.funding_strat, self.scalp_strat, self.vwap_strat, self.absorber_strat] if s is not None]
                strat_actor = None
                for s in all_strats:
                    if s.id == pos.strategy_id:
                        strat_actor = s
                        break
                if not strat_actor and self.node.trader:
                    active_trader_strats = list(getattr(self.node.trader, "_strategies", {}).values())
                    if active_trader_strats:
                        strat_actor = active_trader_strats[0]
                if not strat_actor:
                    strat_actor = all_strats[0] if all_strats else None

                if not strat_actor:
                    self.log(f"No strategy actor available for partial close of {actual_coin}", "ERROR")
                    continue

                try:
                    close_order = strat_actor.order_factory.market(
                        instrument_id=pos.instrument_id,
                        order_side=order_side,
                        quantity=close_qty,
                    )
                    strat_actor.submit_order(close_order)
                    partial_done = True
                    self.log(
                        f"🎯 [Scale-Out] Executed {int(pct*100)}% partial close of {actual_coin}: {close_qty.as_double()} contracts ({reason})",
                        "CLOSE",
                    )
                except Exception as p_err:
                    self.log(f"Partial close submission error on {actual_coin}: {p_err}", "ERROR")
                    continue

                if partial_done:
                    entry_px = float(pos.avg_px_open) if hasattr(pos, "avg_px_open") and pos.avg_px_open else (pos.avg_px.as_double() if hasattr(pos, "avg_px") and pos.avg_px else 0.0)
                    tracker = self.trade_manager.get_position(actual_coin)
                    exit_px = tracker.current_price if (tracker and tracker.current_price > 0) else entry_px

                    closed_qty_num = float(close_qty.as_double())
                    pnl_mult = 1.0 if pos.is_long else -1.0
                    tranche_pnl = (exit_px - entry_px) * closed_qty_num * pnl_mult

                    # Journal the realized tranche without removing active position from TradeManager
                    self.trade_manager.record_closed_trade({
                        "coin": actual_coin,
                        "side": "LONG" if pos.is_long else "SHORT",
                        "size": closed_qty_num,
                        "entry_price": entry_px,
                        "exit_price": exit_px,
                        "realized_pnl": tranche_pnl,
                        "roi_pct": round(((exit_px - entry_px) / entry_px * 100.0 * pnl_mult), 2) if entry_px > 0 else 0.0,
                        "strategy": str(pos.strategy_id),
                        "reason": f"{reason} (50% Partial)",
                        "entry_time": getattr(tracker, "entry_time", time.time() - 60.0),
                        "exit_time": time.time(),
                        "duration_seconds": time.time() - getattr(tracker, "entry_time", time.time() - 60.0),
                    })

                    # Update position size in tracker
                    if tracker:
                        tracker.size = max(0.0, tracker.size - closed_qty_num)
                        # LIVE: re-rest protection for the REMAINING size at the ratcheted stop and original TP
                        if not self.paper and tracker.size > 0:
                            try:
                                rem_dec = (Decimal(str(round(tracker.size, instrument.size_precision))) // step) * step
                                if rem_dec > 0:
                                    tp_roi = tracker.take_profit_roi_pct or 0.0
                                    tp_px_rem = tracker.entry_price * (1 + tp_roi / 100.0) if pos.is_long else tracker.entry_price * (1 - tp_roi / 100.0)
                                    self._place_exchange_protection(
                                        strat_actor, instrument, "LONG" if pos.is_long else "SHORT",
                                        instrument.make_qty(rem_dec), float(tracker.stop_price or 0.0), float(tp_px_rem if tp_roi > 0 else 0.0),
                                    )
                            except Exception as rep_err:
                                self.log(f"Protection re-place after partial close failed for {actual_coin}: {rep_err}", "ERROR")

                    # Update paper balance
                    self._paper_realized_pnl = self._get_net_realized_pnl()
                    total_equity = self._paper_starting_balance + self._paper_realized_pnl
                    self.save_paper_state(
                        equity=total_equity,
                        realized_pnl=self._paper_realized_pnl,
                        positions=self.get_open_positions(),
                    )
            return partial_done

    def get_benchmark_impulse(self) -> Tuple[float, float]:
        """Fetch 5m returns for BTC and ETH over the last 3 bars (cached 30s) for cross-asset impulse filtering."""
        now = time.time()
        if (now - self._benchmark_impulse_cache_ts) < 30.0:
            return self._benchmark_impulse_cache

        btc_ret, eth_ret = 0.0, 0.0
        now_ms = int(now * 1000)
        start_ms = now_ms - (30 * 60 * 1000)

        try:
            btc_klines = self.info_client.get_historical_klines("BTC", "5m", start_time_ms=start_ms, end_time_ms=now_ms)
            if btc_klines and len(btc_klines) >= 3:
                open_px = float(btc_klines[-3].get("o", btc_klines[-3].get("c", 0.0)))
                close_px = float(btc_klines[-1].get("c", 0.0))
                if open_px > 0:
                    btc_ret = ((close_px - open_px) / open_px) * 100.0
        except Exception as e:
            self.log(f"BTC impulse fetch error: {e}", "DEBUG")

        try:
            eth_klines = self.info_client.get_historical_klines("ETH", "5m", start_time_ms=start_ms, end_time_ms=now_ms)
            if eth_klines and len(eth_klines) >= 3:
                open_px = float(eth_klines[-3].get("o", eth_klines[-3].get("c", 0.0)))
                close_px = float(eth_klines[-1].get("c", 0.0))
                if open_px > 0:
                    eth_ret = ((close_px - open_px) / open_px) * 100.0
        except Exception as e:
            self.log(f"ETH impulse fetch error: {e}", "DEBUG")

        self._benchmark_impulse_cache = (round(btc_ret, 3), round(eth_ret, 3))
        self._benchmark_impulse_cache_ts = now
        return self._benchmark_impulse_cache

    def get_coin_intraday_context(self, coin: str) -> Dict[str, Any]:
        """Fetch last 15 5m bars for coin (cached 60s) to compute RSI(14) and 3-bar momentum return."""
        now = time.time()
        cached = self._coin_kline_cache.get(coin)
        if cached and (now - cached[0]) < 60.0:
            return cached[1]

        now_ms = int(now * 1000)
        start_ms = now_ms - (90 * 60 * 1000)
        res = {"rsi_5m": 50.0, "ret_3bar_pct": 0.0, "has_data": False}

        try:
            klines = self.info_client.get_historical_klines(coin, "5m", start_time_ms=start_ms, end_time_ms=now_ms)
            if klines and len(klines) >= 14:
                closes = [float(k.get("c", 0.0)) for k in klines]
                # 3-bar return (15m momentum)
                if len(closes) >= 3 and closes[-3] > 0:
                    res["ret_3bar_pct"] = ((closes[-1] - closes[-3]) / closes[-3]) * 100.0

                # RSI(14) calculation in pure Python
                diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
                gains = [d if d > 0 else 0.0 for d in diffs[-14:]]
                losses = [-d if d < 0 else 0.0 for d in diffs[-14:]]
                avg_gain = sum(gains) / 14.0
                avg_loss = sum(losses) / 14.0
                if avg_loss == 0:
                    res["rsi_5m"] = 100.0
                else:
                    rs = avg_gain / avg_loss
                    res["rsi_5m"] = round(100.0 - (100.0 / (1.0 + rs)), 2)
                res["has_data"] = True
        except Exception as e:
            self.log(f"Intraday context fetch error for {coin}: {e}", "DEBUG")

        self._coin_kline_cache[coin] = (now, res)
        return res

    def close_all_positions(self, reason: str = "Emergency Flatten") -> List[str]:
        """Emergency market close across all open positions with pre-close order sweep."""
        with self._lock:
            open_positions = self.get_open_positions()

            # First sweep and cancel all open orders across the cache for every active position BEFORE dispatching market closes.
            if self.node and self.node.cache:
                for pos in open_positions:
                    actual_coin = pos.instrument_id.symbol.value.split("-")[0].upper()
                    try:
                        all_strats = [s for s in [self.momentum_strat, self.continuation_strat, self.funding_strat, self.scalp_strat, self.vwap_strat, self.absorber_strat] if s is not None]
                        for o in list(self.node.cache.orders_open(instrument_id=pos.instrument_id)):
                            for strat in all_strats:
                                try:
                                    strat.cancel_order(o)
                                except Exception as cancel_err:
                                    self.log(f"Failed to cancel order {getattr(o, 'client_order_id', o)} via {getattr(strat, 'id', strat)}: {cancel_err}", "DEBUG")
                            self.log(f"Pre-close atomically cancelled resting order {o.client_order_id} on {actual_coin}", "INFO")
                    except Exception as err:
                        self.log(f"Pre-close sweep error on {actual_coin}: {err}", "WARN")

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
            min_duration_seconds=2700.0,  # 45 minutes breathing room: allow trades to develop!
        )

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Retrieve quantitative performance metrics from closed trades."""
        return self.analytics.get_metrics(self.trade_manager.get_closed_trades())

    def generate_performance_report(self, output_file: Optional[str] = None) -> str:
        """Generate quantitative performance report in Markdown."""
        return self.analytics.generate_markdown_report(self.trade_manager.get_closed_trades(), output_file=output_file)


    def _ai_sentinel_watchdog_loop(self) -> None:
        """In-process real-time trade sentry auditing active positions every 10s."""
        self.log(f"🛡️ [AI Sentinel] Real-time active trade watchdog started ({int(self.watchdog_interval)}s cycle).", "INFO")
        while self._is_running:
            loop_start = time.time()
            try:
                open_positions = self.get_open_positions()
                self._last_watchdog_audit = time.time()
                self._update_ai_status(open_count=len(open_positions))

                # Synchronize trade_manager, guard, and in-flight close tracker with actual open positions
                open_coins = {pos.instrument_id.symbol.value.split("-")[0].upper() for pos in open_positions}
                now_audit = time.time()
                for c in list(self._in_flight_closes.keys()):
                    if c not in open_coins or (now_audit - self._in_flight_closes[c]) > 600.0:
                        self._in_flight_closes.pop(c, None)

                # Cancel open orders on instruments with no active position (orphan sweep)
                try:
                    if self.node and self.node.cache:
                        position_instrument_ids = {pos.instrument_id for pos in open_positions}
                        now_ts = time.time()
                        for orphan_order in list(self.node.cache.orders_open()):
                            if orphan_order.instrument_id not in position_instrument_ids:
                                order_age = 999.0
                                if hasattr(orphan_order, "ts_init") and orphan_order.ts_init:
                                    order_age = now_ts - (orphan_order.ts_init / 1e9)
                                order_id_str = str(getattr(orphan_order, "client_order_id", getattr(orphan_order, "id", "")))
                                if order_id_str in self._cancelled_orphan_ids:
                                    continue
                                is_pending_guard = hasattr(self, "guard") and order_id_str in getattr(self.guard, "pending_orders", {})
                                if order_age < 30.0:
                                    continue  # Allow legitimate pending entry orders time to execute
                                if order_age < 60.0 and is_pending_guard:
                                    continue
                                if hasattr(self, "guard") and hasattr(self.guard, "pending_orders"):
                                    self.guard.pending_orders.pop(order_id_str, None)
                                orphan_coin = orphan_order.instrument_id.symbol.value.split("-")[0].upper()
                                all_strats = [s for s in [self.momentum_strat, self.continuation_strat, self.funding_strat, self.scalp_strat, self.vwap_strat, self.absorber_strat] if s is not None]
                                for strat in all_strats:
                                    try:
                                        strat.cancel_order(orphan_order)
                                    except Exception as cancel_err:
                                        self.log(f"Failed to cancel orphan order via {getattr(strat, 'id', strat)}: {cancel_err}", "DEBUG")
                                self._cancelled_orphan_ids.add(order_id_str)
                                self.log(f"🧹 [Watchdog] Cancelled orphaned order {orphan_order.client_order_id} on {orphan_coin} (no active position)", "INFO")
                except Exception as orphan_err:
                    self.log(f"Orphan order sweep error: {orphan_err}", "WARN")

                if hasattr(self, "trade_manager") and self.trade_manager:
                    self.trade_manager.sync_active_positions(open_coins)
                if hasattr(self, "guard") and self.guard:
                    self.guard.sync_open_positions(open_positions)

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

                        # 2. Update TradeManager with proportional regime-scaled targets around base 2.80% / 1.20%
                        coin_atr = self.coin_atr_pct.get(coin, 1.2)
                        reg = getattr(getattr(self, "regime_manager", None), "current_regime", None)
                        reg_name = reg.regime if reg else "CHOPPY_MEAN_REVERTING_RANGE"
                        base_tp = getattr(self.trade_manager, "take_profit_roi_pct", 2.80)
                        base_be = getattr(self.trade_manager, "breakeven_roi_pct", 1.20)
                        if reg_name == "BEAR_MARKET_FLUSH":
                            tp_target = round(base_tp * 1.15, 2) if side == "SHORT" else round(base_tp * 0.85, 2)
                            be_roi = base_be
                        elif reg_name == "BULL_MOMENTUM_EXPANSION":
                            tp_target = round(base_tp * 1.15, 2) if side == "LONG" else round(base_tp * 0.85, 2)
                            be_roi = base_be
                        elif reg_name == "CHOPPY_MEAN_REVERTING_RANGE":
                            tp_target = round(base_tp * 0.90, 2)
                            be_roi = round(base_be * 0.90, 2)
                        else:
                            tp_target = base_tp
                            be_roi = base_be

                        # Preserve explicit prospect TP if set on position tracker
                        existing_tracker = self.trade_manager.get_position(coin)
                        if existing_tracker and getattr(existing_tracker, "has_explicit_take_profit", False) and existing_tracker.take_profit_roi_pct:
                            tp_target = existing_tracker.take_profit_roi_pct

                        initial_sl = None
                        prospect_data = self.prospects.get(coin)
                        if prospect_data and prospect_data.get("bias", "").upper() == side:
                            p_sl = float(prospect_data.get("stop_loss", 0.0))
                            if p_sl > 0:
                                if side == "LONG" and p_sl < cur_px:
                                    initial_sl = p_sl
                                elif side == "SHORT" and p_sl > cur_px:
                                    initial_sl = p_sl

                        if initial_sl is None:
                            sl_mult = 1.018 if side == "SHORT" else 0.982  # 1.8% default safety stop
                            initial_sl = entry_px * sl_mult

                        action = self.trade_manager.update_position(
                            coin=coin,
                            side=side,
                            size=qty,
                            entry_price=entry_px,
                            mark_price=cur_px,
                            strategy=strat_name,
                            entry_time=entry_time,
                            atr_pct=coin_atr,
                            take_profit_roi_pct=tp_target,
                            breakeven_roi_pct=be_roi,
                            stop_price=initial_sl,
                        )

                        # 2.1 Regime Transition Shield: Protect trend trades when macro regime shifts
                        reg_breadth = getattr(reg, "breadth_pct", 50.0) if reg else 50.0
                        if "Trend" in strat_name:
                            tracker_obj = self.trade_manager.get_position(coin)
                            if tracker_obj:
                                if side == "LONG" and (reg_name in ("CHOPPY_MEAN_REVERTING_RANGE", "BEAR_MARKET_FLUSH") or reg_breadth < 50.0):
                                    if tracker_obj.current_roi >= 1.0:
                                        be_stop = entry_px * 1.001
                                        if tracker_obj.stop_price is None or be_stop > tracker_obj.stop_price:
                                            tracker_obj.stop_price = be_stop
                                            tracker_obj.breakeven_triggered = True
                                            self.log(f"🛡️ [Regime Shield] Ratcheted {coin} LONG stop to breakeven ${be_stop:.4f} (Regime: {reg_name}, Breadth: {reg_breadth:.1f}%)", "INFO")
                                elif side == "SHORT" and (reg_name in ("BULL_MOMENTUM_EXPANSION", "NEGATIVE_FUNDING_SHORT_SQUEEZE") or reg_breadth >= 60.0):
                                    if tracker_obj.current_roi >= 1.0:
                                        be_stop = entry_px * 0.999
                                        if tracker_obj.stop_price is None or be_stop < tracker_obj.stop_price:
                                            tracker_obj.stop_price = be_stop
                                            tracker_obj.breakeven_triggered = True
                                            self.log(f"🛡️ [Regime Shield] Ratcheted {coin} SHORT stop to breakeven ${be_stop:.4f} (Regime: {reg_name}, Breadth: {reg_breadth:.1f}%)", "INFO")

                        # 3. Trade Manager risk evaluation (MAE, trailing stop, breakeven, stagnant, partial scale-out)
                        if getattr(action, "should_partial_close", False):
                            self.log(f"🎯 [Trade Manager] PARTIAL TRIGGER: {coin} - {action.reason}", "TRIGGER")
                            self.partial_close_position(coin, pct=getattr(action, "partial_close_pct", 0.50), reason=action.reason)

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
                            if can_exit and action.roi <= -1.5 and asks_depth >= 20000 and bids_depth >= 20000:
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

                        # 6. Real-Time Dashboard Microstructure Risk Audit
                        if hasattr(self, "analytics_pipeline") and self.analytics_pipeline:
                            pnl_roi = action.roi if hasattr(action, "roi") else 0.0
                            micro_audit = self.analytics_pipeline.audit_position_risk(
                                coin=coin,
                                side=side,
                                pnl_pct=pnl_roi,
                            )
                            if micro_audit.is_toxic:
                                can_exit, _ = self.trade_manager.check_exit_allowed(coin, is_emergency=False)
                                if can_exit:
                                    reason = f"Dashboard Microstructure Toxic Risk: {'; '.join(micro_audit.toxic_reasons)}"
                                    self.log(f"🚨 [Dashboard Sentinel] TRIGGER: {coin} - {reason}", "TRIGGER")
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
                                venue_order_id=None,
                                command_id=UUID4(),
                                ts_init=time.time_ns(),
                            )
                            self.node.trader.execute(cancel_cmd)
                        except Exception as cancel_err:
                            self.log(f"Failed to cancel stale order {stale.order_id}: {cancel_err}", "DEBUG")
                        self.guard.acknowledge_order_cancelled(stale.order_id)

                # 2. Proactive Autonomous Microstructure Scanner (High-Frequency Sentry)
                # Evaluates L2 depth skew on top perpetuals to enter high-conviction orderbook scalps
                # Strictly rate-limited to at most once per 120s and capped to max 1 concurrent scalp
                now_ts = time.time()
                open_scalps = [p for p in self.get_open_positions() if "OrderBook" in str(getattr(p, "strategy_id", ""))]
                if (
                    self.scalp_strat
                    and self.node
                    and hasattr(self.node, "cache")
                    and self.node.cache
                    and len(open_scalps) == 0
                    and len(self.get_open_positions()) < self.guard.max_total_open_positions
                    and (now_ts - self._last_proactive_scalp_scan) >= 120.0
                ):
                    self._last_proactive_scalp_scan = now_ts
                    scan_coins = self.top_coins[:8] if self.top_coins else list(self.prospects.keys())[:8]
                    for sc_coin in scan_coins:
                        if len(self.get_open_positions()) >= self.guard.max_total_open_positions:
                            break
                        if len([p for p in self.get_open_positions() if "OrderBook" in str(getattr(p, "strategy_id", ""))]) >= 1:
                            break
                        instr_key = f"{sc_coin}-USD-PERP.HYPERLIQUID"
                        instrument = self.scalp_strat.instruments_map.get(instr_key) if (self.scalp_strat and hasattr(self.scalp_strat, "instruments_map")) else None
                        if not instrument:
                            for other_s in [self.momentum_strat, self.continuation_strat, self.funding_strat, self.vwap_strat, self.absorber_strat]:
                                if other_s and hasattr(other_s, "instruments_map") and instr_key in other_s.instruments_map:
                                    instrument = other_s.instruments_map[instr_key]
                                    if self.scalp_strat and hasattr(self.scalp_strat, "instruments_map"):
                                        self.scalp_strat.instruments_map[instr_key] = instrument
                                    break
                        if not instrument and self.node and hasattr(self.node, "cache") and self.node.cache:
                            try:
                                instrument = self.node.cache.instrument(InstrumentId.from_str(instr_key))
                                if instrument and self.scalp_strat and hasattr(self.scalp_strat, "instruments_map"):
                                    self.scalp_strat.instruments_map[instr_key] = instrument
                            except Exception:
                                pass
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
                                # Require realistic book depth on both sides (>= $2k)
                                if b_depth >= 2000 and a_depth >= 2000:
                                    tick_size = instrument.price_increment.as_double()
                                    skew_ratio = b_depth / a_depth
                                    skew_threshold = max(2.0, getattr(self.scalp_strat.scalp_config, "skew_threshold", 2.0))
                                    p_bias = self.prospects.get(sc_coin, {}).get("bias")

                                    if skew_ratio >= skew_threshold and p_bias != "SHORT":
                                        wall_px = float(bids[0]["px"])
                                        min_sl_dist = wall_px * 0.008  # 0.8% stop
                                        min_tp_dist = wall_px * 0.015  # 1.5% take profit
                                        entry_px = wall_px
                                        sl_dist = max(self.scalp_strat.scalp_config.stop_ticks * tick_size, min_sl_dist)
                                        tp_dist = max(self.scalp_strat.scalp_config.take_profit_ticks * tick_size, min_tp_dist)
                                        sl_px = wall_px - sl_dist
                                        tp_px = entry_px + tp_dist
                                        self.scalp_strat._execute_scalp(instrument, OrderSide.BUY, entry_px, sl_px, tp_px, skew_ratio)
                                        break
                                    elif (a_depth / b_depth) >= skew_threshold and p_bias != "LONG":
                                        skew_rev = a_depth / b_depth
                                        wall_px = float(asks[0]["px"])
                                        min_sl_dist = wall_px * 0.008  # 0.8% stop
                                        min_tp_dist = wall_px * 0.015  # 1.5% take profit
                                        entry_px = wall_px
                                        sl_dist = max(self.scalp_strat.scalp_config.stop_ticks * tick_size, min_sl_dist)
                                        tp_dist = max(self.scalp_strat.scalp_config.take_profit_ticks * tick_size, min_tp_dist)
                                        sl_px = wall_px + sl_dist
                                        tp_px = entry_px - tp_dist
                                        self.scalp_strat._execute_scalp(instrument, OrderSide.SELL, entry_px, sl_px, tp_px, skew_rev)
                                        break

                # Also process any bridge commands if written externally
                self._check_external_bridge_commands()

            except Exception as e:
                self.log(f"AI Sentinel watchdog loop notice: {e}", "WARN")

            elapsed = time.time() - loop_start
            sleep_time = max(1.0, self.watchdog_interval - elapsed)
            time.sleep(sleep_time)

    def _process_command(self, cmd: Dict[str, Any]) -> None:
        """Process a single execution command dictionary."""
        action = cmd.get("action")
        if action == "CLOSE_POSITION":
            coin = cmd.get("coin")
            if not coin and cmd.get("target_coins"):
                for tc in cmd.get("target_coins"):
                    self.close_position(tc, reason=cmd.get("reason", "Bridge Close Command"))
            elif coin:
                reason = cmd.get("reason", "Bridge Close Command")
                self.close_position(coin, reason=reason)
        elif action in ("FLATTEN_ALL", "CLOSE_ALL"):
            reason = cmd.get("reason", "Autonomous AI Emergency Flatten")
            self.log(f"🚨 [AI Sentinel Bridge] Executing emergency FLATTEN ALL: {reason}", "TRIGGER")
            self.close_all_positions(reason=reason)
        elif action == "CANCEL_ORPHANS":
            self.log("🧹 [AI Sentinel Bridge] Sweeping all orphaned orders on flat instruments", "INFO")
            try:
                if self.node and self.node.cache:
                    position_instrument_ids = {pos.instrument_id for pos in self.get_open_positions()}
                    for orphan_order in list(self.node.cache.orders_open()):
                        if orphan_order.instrument_id not in position_instrument_ids:
                            all_strats = [s for s in [self.momentum_strat, self.continuation_strat, self.funding_strat, self.scalp_strat, self.vwap_strat, self.absorber_strat] if s is not None]
                            for strat in all_strats:
                                try:
                                    strat.cancel_order(orphan_order)
                                except Exception as cancel_err:
                                    self.log(f"Failed to cancel orphan order: {cancel_err}", "DEBUG")
            except Exception as e:
                self.log(f"Orphan sweep error: {e}", "WARN")
        elif action in ("TRIGGER_SCAN", "TRIGGER_PROSPECTOR_SCAN", "SCAN_AND_TRADE"):
            self.log("🔍 Triggered immediate on-demand Prospector market scan", "INFO")
            self._force_prospector_scan = True
        elif action in ("ENTER_PROSPECT", "OPEN_POSITION"):
            self.execute_prospect_entry(cmd)
        elif action in ("RESET_CIRCUIT_BREAKER", "RESET_BREAKER"):
            if hasattr(self, "guard") and self.guard:
                self.guard.reset_circuit_breaker()
                self.log("🛡️ [Risk Guard] Circuit breaker manually reset. Drawdown window restarted.", "INFO")

    def _check_external_bridge_commands(self) -> None:
        """
        Drain and execute all commands from the in-memory event bus first (zero disk latency),
        then check and clear external bridge/ai_commands.json if present.
        """
        # 1. First drain and process all commands from self.command_queue in-memory with zero disk latency
        while not self.command_queue.empty():
            try:
                cmd = self.command_queue.get_nowait()
                if isinstance(cmd, dict):
                    self._process_command(cmd)
            except queue.Empty:
                break
            except Exception as q_err:
                self.log(f"In-memory command execution error: {q_err}", "WARN")

        # 2. Process external commands from file if present and non-empty
        if not os.path.exists(self.ai_commands_path):
            return
        try:
            with open(self.ai_commands_path, "r") as f:
                cmds = json.load(f)
            if not cmds:
                return

            if isinstance(cmds, dict):
                cmds = [cmds]

            for cmd in cmds:
                if isinstance(cmd, dict):
                    self._process_command(cmd)

            tmp_cmds = f"{self.ai_commands_path}.tmp"
            with open(tmp_cmds, "w") as f:
                json.dump([], f)
            os.replace(tmp_cmds, self.ai_commands_path)
        except Exception as e:
            self.log(f"External bridge commands error: {e}", "WARN")

    def execute_prospect_entry(self, prospect: Dict[str, Any]) -> bool:
        """
        Execute a structured high-conviction prospect trade with bracket orders.
        """
        coin = prospect.get("coin", "").upper()
        bias = prospect.get("bias", "LONG").upper()
        score = prospect.get("conviction_score", 0)
        strat_hint = prospect.get("strategy", "SMC Trend")

        # Primary execution engine: HighConvictionMomentumStrategy
        # Route setup to the appropriate strategy actor, defaulting to HighConvictionMomentumStrategy
        strat_hint_lower = str(strat_hint).lower()
        if self.momentum_strat and (
            any(k in strat_hint_lower for k in ("momentum", "squeeze", "breakout", "highconviction", "volume", "vwap", "trend", "smc"))
            or not ("funding" in strat_hint_lower or "absorber" in strat_hint_lower)
        ):
            strat_actor = self.momentum_strat
            strat_name = "HighConvictionMomentum"
        elif "funding" in strat_hint_lower and self.funding_strat:
            strat_actor = self.funding_strat
            strat_name = "HourlyFundingFade"
        elif any(k in strat_hint_lower for k in ("vwap", "burst", "volume")) and self.vwap_strat:
            strat_actor = self.vwap_strat
            strat_name = "VwapOiMomentum"
        elif any(k in strat_hint_lower for k in ("liquidation", "absorber")) and self.absorber_strat:
            strat_actor = self.absorber_strat
            strat_name = "LiquidationAbsorber"
        elif any(k in strat_hint_lower for k in ("scalp", "book", "imbalance")) and self.scalp_strat:
            strat_actor = self.scalp_strat
            strat_name = "OrderBookImbalance"
        elif any(k in strat_hint_lower for k in ("smc", "trend", "continuation", "divergence", "breakdown")) and self.continuation_strat:
            strat_actor = self.continuation_strat
            strat_name = "TrendContinuationSMC"
        elif self.momentum_strat:
            strat_actor = self.momentum_strat
            strat_name = "HighConvictionMomentum"
        else:
            strat_actor = self.continuation_strat or self.scalp_strat or self.vwap_strat or self.funding_strat or self.absorber_strat
            strat_name = getattr(strat_actor, "__class__", type("")).__name__ if strat_actor else "Unknown"

        if not strat_actor or not hasattr(strat_actor, "instruments_map"):
            self.log(f"🔍 [Prospect Debug] {coin}: No strat_actor ({strat_actor}) or instruments_map", "INFO")
            return False

        instr_key = f"{coin}-USD-PERP.HYPERLIQUID"
        instrument = strat_actor.instruments_map.get(instr_key)
        if not instrument:
            for other_strat in [self.momentum_strat, self.continuation_strat, self.funding_strat, self.vwap_strat, self.scalp_strat, self.absorber_strat]:
                if other_strat and hasattr(other_strat, "instruments_map") and instr_key in other_strat.instruments_map:
                    instrument = other_strat.instruments_map[instr_key]
                    break

        if not instrument and self.node and hasattr(self.node, "cache") and self.node.cache:
            try:
                instrument = self.node.cache.instrument(InstrumentId.from_str(instr_key))
                if instrument and hasattr(strat_actor, "instruments_map"):
                    strat_actor.instruments_map[instr_key] = instrument
            except Exception as inst_err:
                self.log(f"Cache instrument lookup error for {instr_key}: {inst_err}", "DEBUG")

        if not instrument:
            # Dynamically construct instrument using get_hyperliquid_perp(coin)
            try:
                from src.utils.instruments import get_hyperliquid_perp
                instrument = get_hyperliquid_perp(coin)
                if instrument:
                    if self.node and hasattr(self.node, "cache") and self.node.cache:
                        self.node.cache.add_instrument(instrument)
                        if hasattr(self.node, "trader") and hasattr(self.node.trader, "_exec_engine"):
                            for client_obj in getattr(self.node.trader._exec_engine, "_clients", {}).values():
                                if hasattr(client_obj, "exchange"):
                                    try:
                                        client_obj.exchange.add_instrument(instrument)
                                    except Exception:
                                        pass
                        if hasattr(self.node, "trader") and hasattr(self.node.trader, "_data_engine"):
                            for data_client in getattr(self.node.trader._data_engine, "_clients", {}).values():
                                if hasattr(data_client, "_cache"):
                                    try:
                                        data_client._cache.add_instrument(instrument)
                                    except Exception:
                                        pass
                    if hasattr(strat_actor, "instruments_map"):
                        strat_actor.instruments_map[instr_key] = instrument
                    self.log(f"✨ Dynamically constructed and registered instrument in cache for {coin}", "INFO")
            except Exception as dyn_err:
                self.log(f"Dynamic instrument construction error for {coin}: {dyn_err}", "WARN")

        if not instrument:
            self.log(f"🔍 [Prospect Debug] {coin}: Instrument {instr_key} not found in cache", "INFO")
            return False

        # Direction Reversal Check: If an opposing position is active on the coin, flatten it if high conviction
        open_positions_snapshot = self.get_open_positions()
        for pos in open_positions_snapshot:
            c = pos.instrument_id.symbol.value.split("-")[0].upper()
            if c == coin:
                is_pos_long = getattr(pos, "is_long", False) or str(getattr(pos, "side", "")).upper() in ("BUY", "LONG")
                is_opposing = (bias == "LONG" and not is_pos_long) or (bias == "SHORT" and is_pos_long)
                if is_opposing and score >= 80:
                    self.log(
                        f"🔄 [Direction Reversal] Flattening opposing {pos.side} position on {coin} to reverse into {bias} (Conviction: {score})",
                        "TRIGGER",
                    )
                    self.close_position(coin, reason=f"Direction reversal to {bias} (Conviction {score})")
                    time.sleep(0.5)
                    break
                else:
                    # Same direction already open or low conviction reversal, avoid stacking
                    self.log(f"🔍 [Prospect Debug] {coin}: Already open in same direction", "INFO")
                    return False

        if self.node and self.node.cache:
            now_ns = time.time_ns()
            active_resting_orders = []
            for o in self.node.cache.orders_open(instrument_id=instrument.id):
                o_ts = getattr(o, "ts_init", 0)
                order_age_s = (now_ns - o_ts) / 1e9 if o_ts else 0.0
                if order_age_s < 90.0:
                    active_resting_orders.append(o)
                else:
                    self.log(
                        f"⚠️ [Zombie Order Bypass] Ignoring stale open order {getattr(o, 'client_order_id', o)} "
                        f"on {coin} (age {order_age_s:.0f}s >= 90s)",
                        "WARN",
                    )
            if len(active_resting_orders) > 0:
                self.log(f"🔍 [Prospect Debug] {coin}: Resting open orders already exist", "INFO")
                return False

        if self.guard.is_in_cooldown(coin):
            self.log(f"🔍 [Prospect Debug] {coin}: Under anti-churn cooldown", "INFO")
            return False

        # 1. 5-Minute BTC / ETH Cross-Asset Impulse Gate
        if coin not in ("BTC", "ETH"):
            btc_ret, eth_ret = self.get_benchmark_impulse()
            if bias == "LONG" and (btc_ret <= -0.35 or eth_ret <= -0.45):
                self.log(
                    f"🚫 [Macro Impulse Gate] Blocked LONG {coin}: BTC ({btc_ret:+.2f}%) or ETH ({eth_ret:+.2f}%) dumping on 5m",
                    "WARN",
                )
                self.log_missed_opportunity(
                    coin=coin,
                    strategy=strat_name,
                    reason=f"Macro Impulse Gate: BTC {btc_ret:+.2f}%, ETH {eth_ret:+.2f}% dumping",
                    metrics={"btc_ret": btc_ret, "eth_ret": eth_ret},
                )
                return False
            elif bias == "SHORT" and (btc_ret >= 0.35 or eth_ret >= 0.45):
                self.log(
                    f"🚫 [Macro Impulse Gate] Blocked SHORT {coin}: BTC ({btc_ret:+.2f}%) or ETH ({eth_ret:+.2f}%) pumping on 5m",
                    "WARN",
                )
                self.log_missed_opportunity(
                    coin=coin,
                    strategy=strat_name,
                    reason=f"Macro Impulse Gate: BTC {btc_ret:+.2f}%, ETH {eth_ret:+.2f}% pumping",
                    metrics={"btc_ret": btc_ret, "eth_ret": eth_ret},
                )
                return False

        # 2. Intraday 15m Trend & Exhaustion Alignment (RSI 14 + 3-bar return)
        intraday = self.get_coin_intraday_context(coin)
        if intraday.get("has_data", False):
            rsi = intraday["rsi_5m"]
            ret_3b = intraday["ret_3bar_pct"]
            if bias == "LONG":
                if rsi > 74.0:
                    self.log(f"🚫 [Exhaustion Gate] Blocked LONG {coin}: 5m RSI({rsi:.1f}) > 74.0 (Overbought)", "WARN")
                    return False
                if ret_3b < -0.60:
                    self.log(f"🚫 [Exhaustion Gate] Blocked LONG {coin}: Falling knife on 15m ({ret_3b:+.2f}%)", "WARN")
                    return False
            elif bias == "SHORT":
                if rsi < 26.0:
                    self.log(f"🚫 [Exhaustion Gate] Blocked SHORT {coin}: 5m RSI({rsi:.1f}) < 26.0 (Oversold)", "WARN")
                    return False
                if ret_3b > 0.60:
                    self.log(f"🚫 [Exhaustion Gate] Blocked SHORT {coin}: Chasing vertical pump on 15m ({ret_3b:+.2f}%)", "WARN")
                    return False

        side = OrderSide.BUY if bias == "LONG" else OrderSide.SELL
        mark_px = float(prospect.get("mark_price", 0.0))
        if mark_px <= 0:
            return False

        best_bid: Optional[float] = None
        best_ask: Optional[float] = None

        # Robust Pre-Entry Spread (<= 25 bps) and Depth (>= $2k) Gates to protect trade execution
        try:
            l2_snap = self.info_client.get_l2_snapshot(coin)
            if l2_snap.get("status") == "SUCCESS":
                bids = l2_snap.get("bids", [])
                asks = l2_snap.get("asks", [])
                if bids and asks:
                    best_bid = float(bids[0]["px"])
                    best_ask = float(asks[0]["px"])
                    spread_bps = ((best_ask - best_bid) / best_bid) * 10000.0 if best_bid > 0 else 999.0
                    bid_depth_usd = sum(float(b["sz"]) * float(b["px"]) for b in bids[:5])
                    ask_depth_usd = sum(float(a["sz"]) * float(a["px"]) for a in asks[:5])

                    if spread_bps > 25.0:
                        self.log(
                            f"🚫 [Pre-Entry Gate] Blocked {bias} {coin}: Spread {spread_bps:.1f} bps > 25.0 bps limit. "
                            f"Preventing spread slippage.",
                            "WARN",
                        )
                        self.log_missed_opportunity(
                            coin=coin,
                            strategy=strat_name,
                            reason=f"Pre-Entry Gate: Spread {spread_bps:.1f} bps > 25 bps",
                            metrics={"spread_bps": spread_bps, "bid_depth": bid_depth_usd, "ask_depth": ask_depth_usd},
                        )
                        return False

                    if min(bid_depth_usd, ask_depth_usd) < 500.0:
                        self.log(
                            f"🚫 [Pre-Entry Gate] Blocked {bias} {coin}: Depth ${min(bid_depth_usd, ask_depth_usd):,.0f} < $500 limit. "
                            f"Preventing illiquid slippage trap.",
                            "WARN",
                        )
                        self.log_missed_opportunity(
                            coin=coin,
                            strategy=strat_name,
                            reason=f"Pre-Entry Gate: Insufficient depth (${min(bid_depth_usd, ask_depth_usd):,.0f} < $500)",
                            metrics={"spread_bps": spread_bps, "bid_depth": bid_depth_usd, "ask_depth": ask_depth_usd},
                        )
                        return False
        except Exception as l2_err:
            self.log(f"Pre-entry L2 check error for {coin}: {l2_err}", "DEBUG")

        if (best_bid is None or best_ask is None) and self.node and self.node.cache:
            try:
                q = self.node.cache.quote_tick(instrument.id)
                if q:
                    if best_bid is None and q.bid_price:
                        best_bid = q.bid_price.as_double()
                    if best_ask is None and q.ask_price:
                        best_ask = q.ask_price.as_double()
            except Exception:
                pass

        # 1m Volume Burst Detection: Verify active volume momentum expansion on breakout entry
        try:
            now_ms = int(time.time() * 1000)
            klines_1m = self.info_client.get_historical_klines(coin, "1m", start_time_ms=now_ms - (15 * 60 * 1000), end_time_ms=now_ms)
            if klines_1m and len(klines_1m) >= 5:
                vols = [float(k.get("v", 0.0)) for k in klines_1m]
                avg_vol = sum(vols[:-1]) / max(1, len(vols) - 1)
                latest_vol = vols[-1]
                if avg_vol > 0 and latest_vol >= (avg_vol * 1.2):
                    self.log(f"⚡ [Volume Burst] {coin} 1m volume expansion confirmed ({latest_vol:,.0f} vs avg {avg_vol:,.0f})", "INFO")
        except Exception as vol_err:
            self.log(f"Volume burst check notice for {coin}: {vol_err}", "DEBUG")

        sl_px = float(prospect.get("stop_loss", 0.0))
        tp_px = float(prospect.get("take_profit", 0.0))

        reg = getattr(getattr(self, "regime_manager", None), "current_regime", None)
        reg_name = reg.regime if reg else "CHOPPY_MEAN_REVERTING_RANGE"

        # Sync regime to PortfolioGuard
        if hasattr(self, "guard") and hasattr(self.guard, "set_market_regime"):
            self.guard.set_market_regime(reg_name)

        if sl_px <= 0 or tp_px <= 0:
            if reg_name == "BEAR_MARKET_FLUSH":
                # In bear flush: short TP 3.5%, SL 1.8%; longs quick scalp 2.0%, tight SL 1.5%
                l_tp, l_sl = 1.020, 0.985
                s_tp, s_sl = 0.965, 1.018
            elif reg_name == "BULL_MOMENTUM_EXPANSION":
                # In bull expansion: long TP 5.0%, SL 2.0%; shorts scalp 2.0%
                l_tp, l_sl = 1.050, 0.980
                s_tp, s_sl = 0.980, 1.020
            elif reg_name == "CHOPPY_MEAN_REVERTING_RANGE":
                l_tp, l_sl = 1.028, 0.985
                s_tp, s_sl = 0.972, 1.015
            else:
                l_tp, l_sl = 1.035, 0.980
                s_tp, s_sl = 0.965, 1.020

            if side == OrderSide.BUY:
                sl_px = round(mark_px * l_sl, instrument.price_precision)
                tp_px = round(mark_px * l_tp, instrument.price_precision)
            else:
                sl_px = round(mark_px * s_sl, instrument.price_precision)
                tp_px = round(mark_px * s_tp, instrument.price_precision)

        risk_per_unit = abs(mark_px - sl_px)
        if risk_per_unit <= 0:
            return False

        equity = self.get_account_cash()
        if equity <= 0 or mark_px <= 0:
            return False

        # Sizing: Deploy entire account balance ($200 base) split across 2 slots
        # 50% margin per position ($100 margin each) utilizing target coin's maximum exchange leverage
        strat_mult = 1.0
        if hasattr(self, "guard") and hasattr(self.guard, "get_strategy_sizing_multiplier"):
            strat_mult = self.guard.get_strategy_sizing_multiplier(strat_name)

        # 50% base allocation per position (scaled down only if in probation 0.4x)
        base_alloc_pct = 0.50
        margin_allocated = min(equity * 0.50, equity * base_alloc_pct * strat_mult)
        coin_max_lev = float(get_coin_max_leverage(coin))  # Native Hyperliquid exchange maximum leverage per coin
        if score < 90:
            eff_lev = min(coin_max_lev, 15.0)
            if eff_lev < coin_max_lev:
                self.log(
                    f"⚡ [Conviction Leverage] {coin}: Conviction score {score} < 90, clamping max leverage {coin_max_lev:.0f}x -> {eff_lev:.0f}x",
                    "INFO",
                )
            coin_max_lev = eff_lev
        else:
            self.log(
                f"🔥 [Conviction Leverage] {coin}: Strong conviction score {score} >= 90, allowing full exchange leverage {coin_max_lev:.0f}x",
                "INFO",
            )
        target_notional = margin_allocated * coin_max_lev

        # Risk budget: dollar loss at the stop must not exceed max_risk_pct_per_trade of equity.
        # The same stop distance is what TradeManager enforces and what the dashboard displays.
        sl_dist_frac = risk_per_unit / mark_px
        max_risk_usd = equity * self.max_risk_pct_per_trade
        if sl_dist_frac > 0 and (target_notional * sl_dist_frac) > max_risk_usd:
            clamped_notional = max_risk_usd / sl_dist_frac
            self.log(
                f"⚖️ [Risk Budget] {coin}: notional ${target_notional:,.0f} -> ${clamped_notional:,.0f} "
                f"(stop {sl_dist_frac*100:.2f}% would risk ${target_notional * sl_dist_frac:,.2f} > ${max_risk_usd:,.2f} budget)",
                "INFO",
            )
            target_notional = clamped_notional
        qty_val = target_notional / mark_px

        # Nautilus lot-step truncation to avoid Cython core crash
        step = Decimal(str(instrument.size_increment.as_double()))
        qty_dec = (Decimal(str(round(qty_val, instrument.size_precision))) // step) * step
        if qty_dec <= 0:
            return False
        quantity = instrument.make_qty(qty_dec)
        if quantity.as_double() <= 0:
            return False

        notional_usd = float(quantity.as_double()) * mark_px
        if notional_usd < 11.0:
            self.log(f"Order notional (${notional_usd:.2f}) below exchange minimum floor of $11.0 USD for {coin}", "DEBUG")
            return False

        open_pos = self.get_open_positions()
        self.guard.sync_open_positions(open_pos)
        self.guard.update_equity(equity)
        can_trade, reason = self.guard.can_open_position(
            strategy_name=strat_name,
            instrument_id=instrument.id,
            side=side,
            proposed_notional_usd=notional_usd,
            current_open_positions_count=len(open_pos),
            open_positions=open_pos,
            leverage=coin_max_lev,
        )
        if not can_trade and ("reached max position limit" in reason or "allocation cap reached" in reason) and len(open_pos) < self.guard.max_total_open_positions:
            for alt_actor, alt_name in [
                (self.momentum_strat, "HighConvictionMomentum"),
                (self.continuation_strat, "TrendContinuationSMC"),
                (self.scalp_strat, "OrderBookImbalance"),
                (self.funding_strat, "HourlyFundingFade"),
                (self.absorber_strat, "LiquidationAbsorber"),
            ]:
                if alt_actor and alt_name != strat_name:
                    alt_ok, alt_reason = self.guard.can_open_position(
                        strategy_name=alt_name,
                        instrument_id=instrument.id,
                        side=side,
                        proposed_notional_usd=notional_usd,
                        current_open_positions_count=len(open_pos),
                        open_positions=open_pos,
                        leverage=coin_max_lev,
                    )
                    if alt_ok:
                        if hasattr(alt_actor, "instruments_map") and instr_key not in alt_actor.instruments_map:
                            alt_actor.instruments_map[instr_key] = instrument
                        strat_actor = alt_actor
                        strat_name = alt_name
                        can_trade = True
                        reason = f"Routed to {alt_name} (primary reached limit)"
                        break

        if not can_trade:
            # If high conviction (>= 88), attempt capital rotation of a stagnant position
            if score >= 88:
                rotation_cand = self.find_capital_rotation_candidate(coin, conviction_score=score)
                if rotation_cand:
                    coin_to_close, rot_reason = rotation_cand
                    self.log(
                        f"🔄 [Capital Rotation] Closing stagnant position {coin_to_close} to rotate capital into {coin} ({rot_reason})",
                        "TRIGGER",
                    )
                    if self.close_position(coin_to_close, reason=rot_reason):
                        time.sleep(0.5)
                        open_pos = self.get_open_positions()
                        self.guard.sync_open_positions(open_pos)
                        can_trade, reason = self.guard.can_open_position(
                            strategy_name=strat_name,
                            instrument_id=instrument.id,
                            side=side,
                            proposed_notional_usd=notional_usd,
                            current_open_positions_count=len(open_pos),
                            open_positions=open_pos,
                            leverage=coin_max_lev,
                        )

            if not can_trade:
                self.log(f"PortfolioGuard skipped prospect {coin} {bias}: {reason}", "INFO")
                return False

        tp_price_obj = instrument.make_price(Decimal(str(round(tp_px, instrument.price_precision))))
        sl_price_obj = instrument.make_price(Decimal(str(round(sl_px, instrument.price_precision))))

        self.log(
            f"🎯 [AI Prospector] EXECUTING {bias} {coin} [{strat_name}]: Qty={quantity} @ ~{mark_px:.4f} | "
            f"SL={sl_price_obj} | TP={tp_price_obj} | Conviction={score}",
            "TRIGGER"
        )

        # Microstructure-optimized entry: Maker post-only limit order to earn maker rebate (-0.010%) instead of paying taker fee (+0.035%)
        # Anchor post-only buys to best_bid (or best_ask - tick_size) and sells to best_ask (or best_bid + tick_size), avoiding cross when mark_px leads
        tick_size = instrument.price_increment.as_double() if hasattr(instrument, "price_increment") else 0.01
        if side == OrderSide.BUY:
            if best_bid is not None and best_ask is not None:
                raw_entry = min(best_bid, best_ask - tick_size)
            elif best_bid is not None:
                raw_entry = best_bid
            elif best_ask is not None:
                raw_entry = best_ask - tick_size
            else:
                raw_entry = mark_px - tick_size
        else:
            if best_bid is not None and best_ask is not None:
                raw_entry = max(best_ask, best_bid + tick_size)
            elif best_ask is not None:
                raw_entry = best_ask
            elif best_bid is not None:
                raw_entry = best_bid + tick_size
            else:
                raw_entry = mark_px + tick_size
        if raw_entry <= 0:
            raw_entry = mark_px
        entry_px = to_hl_price(raw_entry, instrument.price_precision)
        entry_price_obj = instrument.make_price(Decimal(str(entry_px)))

        try:
            if not self.paper and not getattr(strat_actor.cache, "is_backtest", False):
                strat_actor.subscribe_quote_ticks(instrument.id)
        except Exception as sub_err:
            self.log(f"Quote tick subscription notice for {instrument.id}: {sub_err}", "DEBUG")

        try:
            if self.paper:
                # Ensure Sandbox matching engine has a live market price for instant fill
                try:
                    from nautilus_trader.model.data import QuoteTick
                    now_ns = time.time_ns()
                    bp_val = best_bid if best_bid is not None else mark_px * 0.999
                    ap_val = best_ask if best_ask is not None else mark_px * 1.001
                    bp = instrument.make_price(Decimal(str(to_hl_price(bp_val, instrument.price_precision))))
                    ap = instrument.make_price(Decimal(str(to_hl_price(ap_val, instrument.price_precision))))
                    q_sz = instrument.make_qty(Decimal("1000.0"))
                    q_tick = QuoteTick(
                        instrument_id=instrument.id,
                        bid_price=bp,
                        ask_price=ap,
                        bid_size=q_sz,
                        ask_size=q_sz,
                        ts_event=now_ns,
                        ts_init=now_ns,
                    )
                    if self.node and hasattr(self.node, "cache") and self.node.cache:
                        self.node.cache.add_quote_tick(q_tick)
                    if hasattr(self.node, "trader") and hasattr(self.node.trader, "_exec_engine"):
                        for c_obj in getattr(self.node.trader._exec_engine, "_clients", {}).values():
                            if hasattr(c_obj, "exchange"):
                                me = c_obj.exchange.get_matching_engine(instrument.id)
                                if me:
                                    me.process_quote_tick(q_tick)
                except Exception as tick_err:
                    self.log(f"Sandbox quote injection notice: {tick_err}", "DEBUG")

                # Direct market execution in paper mode avoids SandboxExecutionClient maker post-only zombie cancellations
                order = strat_actor.order_factory.market(
                    instrument_id=instrument.id,
                    order_side=side,
                    quantity=quantity,
                )
                strat_actor.submit_order(order)
                self.guard.register_order_submitted(
                    order=order,
                    strategy_name=strat_name,
                    notional_usd=notional_usd,
                )
                entry_px = mark_px
                fill_verified = True
            else:
                try:
                    order = strat_actor.order_factory.limit(
                        instrument_id=instrument.id,
                        order_side=side,
                        quantity=quantity,
                        price=entry_price_obj,
                        time_in_force=TimeInForce.GTC,
                        post_only=True,
                    )
                except TypeError:
                    order = strat_actor.order_factory.limit(
                        instrument_id=instrument.id,
                        order_side=side,
                        quantity=quantity,
                        price=entry_price_obj,
                        time_in_force=TimeInForce.GTC,
                    )
                strat_actor.submit_order(order)
                self.guard.register_order_submitted(
                    order=order,
                    strategy_name=strat_name,
                    notional_usd=notional_usd,
                )

                # Register with TradeManager upon fill verification
                fill_verified = False
                for _ in range(8):
                    time.sleep(0.1)
                    if getattr(order, "is_filled", False):
                        fill_verified = True
                        break
                    open_coins_now = {p.instrument_id.symbol.value.split("-")[0].upper() for p in self.get_open_positions()}
                    if coin in open_coins_now:
                        fill_verified = True
                        break

                if not fill_verified:
                    # If maker post-only does not fill immediately, execute market order to guarantee entry
                    try:
                        strat_actor.cancel_order(order)
                    except Exception:
                        pass
                    if hasattr(self.guard, "pending_orders"):
                        self.guard.pending_orders.pop(str(order.client_order_id), None)
                    try:
                        mkt_order = strat_actor.order_factory.market(
                            instrument_id=instrument.id,
                            order_side=side,
                            quantity=quantity,
                        )
                        strat_actor.submit_order(mkt_order)
                        self.guard.register_order_submitted(
                            order=mkt_order,
                            strategy_name=strat_name,
                            notional_usd=notional_usd,
                        )
                        order = mkt_order
                        for _ in range(10):
                            time.sleep(0.1)
                            if getattr(order, "is_filled", False):
                                fill_verified = True
                                break
                            open_coins_now = {p.instrument_id.symbol.value.split("-")[0].upper() for p in self.get_open_positions()}
                            if coin in open_coins_now:
                                fill_verified = True
                                break
                    except Exception as mkt_err:
                        self.log(f"Market fallback order notice for {coin}: {mkt_err}", "DEBUG")

            if hasattr(self.guard, "pending_orders"):
                self.guard.pending_orders.pop(str(order.client_order_id), None)
            open_pos = self.get_open_positions()
            self.guard.sync_open_positions(open_pos)

            if fill_verified or (coin in {p.instrument_id.symbol.value.split("-")[0].upper() for p in self.get_open_positions()}):
                if hasattr(self, "trade_manager") and self.trade_manager:
                    # Compute explicit target ROI % from entry and tp_px
                    target_tp_roi = None
                    if tp_px > 0 and entry_px > 0:
                        target_tp_roi = round(abs(tp_px - entry_px) / entry_px * 100.0, 2)
                    coin_atr = self.coin_atr_pct.get(coin, 1.2)
                    self.trade_manager.register_position(
                        coin=coin,
                        side=bias,
                        size=float(quantity.as_double()),
                        entry_price=entry_px,
                        strategy=strat_name,
                        entry_time=time.time(),
                        atr_pct=coin_atr,
                        take_profit_roi_pct=target_tp_roi,
                        stop_price=float(sl_px) if sl_px > 0 else None,
                    )
                    self._place_exchange_protection(strat_actor, instrument, bias, quantity, float(sl_px), float(tp_px))
            self._closed_positions_coins.discard(coin)
            self._in_flight_closes.pop(coin, None)
            return True
        except Exception as e:
            # Fallback to direct HyperliquidAgentWallet native signing if live
            if not self.paper and hasattr(self, "agent_wallet") and self.agent_wallet.is_configured:
                try:
                    self.log(f"Falling back to native HyperliquidAgentWallet order placement for {coin}...", "WARN")
                    res = self.agent_wallet.place_order(
                        coin=coin,
                        is_buy=(side == OrderSide.BUY),
                        size=float(quantity.as_double()),
                        limit_px=entry_px,
                        order_type={"limit": {"tif": "Alo"}},
                        reduce_only=False,
                    )
                    if res and res.get("status") in ("filled", "MOCK_PAPER_FILLED"):
                        if hasattr(self, "trade_manager") and self.trade_manager:
                            self.trade_manager.register_position(
                                coin=coin,
                                side=bias,
                                size=float(quantity.as_double()),
                                entry_price=entry_px,
                                strategy=strat_name,
                                entry_time=time.time(),
                                stop_price=float(sl_px) if sl_px > 0 else None,
                            )
                        self._closed_positions_coins.discard(coin)
                        self._in_flight_closes.pop(coin, None)
                        return True
                    elif res and res.get("status") == "ok":
                        self._closed_positions_coins.discard(coin)
                        self._in_flight_closes.pop(coin, None)
                        return True
                except Exception as aw_err:
                    self.log(f"Native AgentWallet fallback order failed on {coin}: {aw_err}", "ERROR")
            self.log(f"Failed to submit prospect order for {coin}: {e}", "ERROR")
            return False

    def _run_prospector_scan(self) -> None:
        """Run a complete 15-minute prospector market scan and execute top setups."""
        self._prospector_scan_count += 1
        now_ts = time.time()
        self._last_prospector_scan = now_ts
        self._next_prospector_scan = now_ts + self.prospector_interval

        # Check auto-recovery for AI fallback engine
        if hasattr(self, "fallback_engine") and self.fallback_engine:
            if self.fallback_engine.check_auto_recovery():
                self.log("🛡️ [AI Fallback] Cooldown elapsed: recovered to HEALTHY AI mode.", "INFO")

        self._update_ai_status(open_count=len(self.get_open_positions()))
        self.log(f"🔍 [AI Prospector] Running {int(self.prospector_interval / 60)}-minute market scan #{self._prospector_scan_count} across top 50 perpetuals...", "INFO")
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
                self.coin_atr_pct[name] = max(0.6, min(3.0, abs(change_24h) * 0.25 + 0.6))
                records.append({
                    "coin": name, "price": px, "funding_apr": funding,
                    "vol_24h": vol_24h, "change_24h": change_24h,
                })

            df = pl.DataFrame(records).sort("vol_24h", descending=True).head(50)

            # 0. Autonomous Market Regime Classification & Dynamic Strategy Tweaking
            regime_info = self.regime_manager.evaluate_universe(df)
            applied_tweaks = self.regime_manager.apply_regime_to_engine(
                regime_info=regime_info,
                guard=self.guard,
                continuation_strat=self.continuation_strat,
                funding_strat=self.funding_strat,
                scalp_strat=self.scalp_strat,
                vwap_strat=self.vwap_strat,
            )
            # Dynamic strategy activation/deactivation hooks:
            # When market breadth > 45% or volatility expands, dynamically re-introduce secondary strategies
            is_secondary_active = (regime_info.breadth_pct >= 45.0) or (regime_info.volatility_score >= 1.5)
            max_sec_pos = 2 if is_secondary_active else 1
            for strat_name_key in ("TrendContinuationSMC", "OrderBookImbalance", "VwapOiMomentum", "HourlyFundingFade"):
                self.guard.set_strategy_max_positions(strat_name_key, max_sec_pos)
            self.guard.set_strategy_max_positions("HighConvictionMomentum", 2)
            self.guard.set_market_regime(regime_info.regime, applied_tweaks, breadth_pct=regime_info.breadth_pct)
            self.log(
                f"🌐 [Market Regime Engine] Active Regime: {regime_info.regime} "
                f"({regime_info.breadth_pct:.0f}% green, avg {regime_info.avg_change_24h:+.1f}%, carry {regime_info.avg_funding_apr:+.1f}% APR). "
                f"Secondary strategies active: {is_secondary_active}. Applied {len(applied_tweaks)} dynamic strategy tweaks: {', '.join(applied_tweaks)}",
                "TRIGGER",
            )
            try:
                with open(self.market_regime_path, "w") as f:
                    json.dump(regime_info.to_dict(), f, indent=2)
            except Exception as reg_err:
                self.log(f"Market regime persistence warning: {reg_err}", "WARN")

            # Stream regime update into 10s AI Sentinel Thoughts
            regime_thought = {
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "category": "REGIME",
                "coin": "MACRO",
                "side": regime_info.regime,
                "roi": 0.0,
                "pnl": 0.0,
                "text": f"Regime: {regime_info.regime} | Breadth {regime_info.breadth_pct:.0f}% | Avg Carry {regime_info.avg_funding_apr:+.1f}% APR. {regime_info.sentiment}",
                "status": "OPTIMAL" if "EXPANSION" in regime_info.regime else "NORMAL",
            }
            self.sentinel_thoughts.append(regime_thought)
            if len(self.sentinel_thoughts) > 50:
                self.sentinel_thoughts.pop(0)

            # Per-coin ATR-scaled Initial SL/TP calculation
            def get_coin_sl_tp(coin_sym: str, mark_price: float, is_long: bool) -> Tuple[float, float]:
                coin_atr = self.coin_atr_pct.get(coin_sym, 1.2)
                sl_pct = max(0.012, min(0.025, (coin_atr * 1.2) / 100.0))
                tp_pct = max(0.025, min(0.055, sl_pct * 2.0))
                if is_long:
                    return round(mark_price * (1.0 - sl_pct), 4), round(mark_price * (1.0 + tp_pct), 4)
                else:
                    return round(mark_price * (1.0 + sl_pct), 4), round(mark_price * (1.0 - tp_pct), 4)

            prospects_list = []
            selected_coins = set()

            is_bull_regime = (
                regime_info.regime in ("BULL_MOMENTUM_EXPANSION", "NEGATIVE_FUNDING_SHORT_SQUEEZE")
                or regime_info.breadth_pct >= 60.0
            )

            # --- Hit-and-Run Group 1: Short Squeeze Pops ---
            # Funding APR < -15% with positive 24h momentum and heavy volume
            squeeze_filter = (
                (pl.col("funding_apr") < -15.0)
                & (pl.col("change_24h") > 1.0)
                & (pl.col("vol_24h") > 5_000_000)
            )
            squeeze_df = df.filter(squeeze_filter).sort("vol_24h", descending=True).head(5)
            squeeze_candidates = []
            for row in squeeze_df.iter_rows(named=True):
                c = row["coin"]
                px = float(row["price"])
                funding_apr = float(row["funding_apr"])
                chg = float(row["change_24h"])
                c_sl, c_tp = get_coin_sl_tp(c, px, is_long=True)
                squeeze_candidates.append({
                    "coin": c,
                    "bias": "LONG",
                    "conviction_score": min(95, int(85 + abs(funding_apr) / 5.0)),
                    "strategy": "Short Squeeze Pop",
                    "mark_price": px,
                    "target_entry": round(px * 0.997, 4),
                    "stop_loss": c_sl,
                    "take_profit": c_tp,
                    "funding_apr_pct": funding_apr,
                    "volume_24h_usd": float(row["vol_24h"]),
                    "rationale": f"Short squeeze pop: Funding APR {funding_apr:.1f}% on ${row['vol_24h']/1e6:.1f}M vol with +{chg:.1f}% 24h gain; rapid squeeze momentum.",
                })

            # --- Hit-and-Run Group 2: Hot Breakout Momentum ---
            # Rapid 24h momentum expansion (>3.5%) with institutional volume (> $10M)
            breakout_filter = (
                (pl.col("change_24h") > 3.5)
                & (pl.col("vol_24h") > 10_000_000)
            )
            breakout_df = df.filter(breakout_filter).sort("change_24h", descending=True).head(5)
            breakout_candidates = []
            for row in breakout_df.iter_rows(named=True):
                c = row["coin"]
                px = float(row["price"])
                funding_apr = float(row["funding_apr"])
                chg = float(row["change_24h"])
                c_sl, c_tp = get_coin_sl_tp(c, px, is_long=True)
                breakout_candidates.append({
                    "coin": c,
                    "bias": "LONG",
                    "conviction_score": min(95, int(82 + chg)),
                    "strategy": "Hot Breakout Momentum",
                    "mark_price": px,
                    "target_entry": round(px * 0.996, 4),
                    "stop_loss": c_sl,
                    "take_profit": c_tp,
                    "funding_apr_pct": funding_apr,
                    "volume_24h_usd": float(row["vol_24h"]),
                    "rationale": f"Hot breakout momentum: +{chg:.1f}% 24h pump on ${row['vol_24h']/1e6:.1f}M vol; high-velocity continuation.",
                })

            # --- Group A: Spot-Led Long Momentum Candidates ---
            if regime_info.regime == "BEAR_MARKET_FLUSH":
                longs_df = df.filter((pl.col("coin").is_in(["BTC", "ETH"])) & (pl.col("change_24h") > 0.5)).sort("vol_24h", descending=True).head(2)
            else:
                long_head = 6 if is_bull_regime else 4
                longs_df = df.filter(
                    (pl.col("change_24h") > 2.0)
                    & (pl.col("funding_apr") < 35.0)
                    & (pl.col("funding_apr") > -15.0)
                    & (pl.col("vol_24h") > 5_000_000)
                ).sort("vol_24h", descending=True).head(long_head)
                if len(longs_df) < 2:
                    longs_df = df.filter(
                        (pl.col("change_24h") > 1.0)
                        & (pl.col("funding_apr") < 35.0)
                        & (pl.col("vol_24h") > 1_000_000)
                    ).sort("vol_24h", descending=True).head(long_head)

            long_candidates = []
            for row in longs_df.iter_rows(named=True):
                c = row["coin"]
                px = float(row["price"])
                funding_apr = float(row["funding_apr"])
                chg = float(row["change_24h"])
                c_sl, c_tp = get_coin_sl_tp(c, px, is_long=True)
                long_candidates.append({
                    "coin": c,
                    "bias": "LONG",
                    "conviction_score": min(95, int(82 + chg)),
                    "strategy": "SMC Trend & Spot Divergence",
                    "mark_price": px,
                    "target_entry": round(px * 0.994, 4),
                    "stop_loss": c_sl,
                    "take_profit": c_tp,
                    "funding_apr_pct": funding_apr,
                    "volume_24h_usd": float(row["vol_24h"]),
                    "rationale": f"Spot-led accumulation: +{chg:.1f}% 24h on ${row['vol_24h']/1e6:.1f}M vol; healthy baseline funding (+{funding_apr:.1f}% APR).",
                })

            # --- Group B: Extreme Funding Fade Shorts Candidates ---
            # In Bull Expansion, require extreme funding (>80% APR) to avoid fighting trend
            fade_thresh = 80.0 if is_bull_regime else 50.0
            fade_head = 2 if is_bull_regime else 5
            shorts_df = df.filter(pl.col("funding_apr") > fade_thresh).sort("funding_apr", descending=True).head(fade_head)
            fade_short_candidates = []
            for row in shorts_df.iter_rows(named=True):
                c = row["coin"]
                px = float(row["price"])
                funding_apr = float(row["funding_apr"])
                c_sl, c_tp = get_coin_sl_tp(c, px, is_long=False)
                fade_short_candidates.append({
                    "coin": c,
                    "bias": "SHORT",
                    "conviction_score": min(95, int(80 + (funding_apr / 20.0))),
                    "strategy": "Hourly Funding Fade",
                    "mark_price": px,
                    "target_entry": round(px * 1.008, 4),
                    "stop_loss": c_sl,
                    "take_profit": c_tp,
                    "funding_apr_pct": funding_apr,
                    "volume_24h_usd": float(row["vol_24h"]),
                    "rationale": f"Crowded long leverage: Funding APR +{funding_apr:.1f}% on ${row['vol_24h']/1e6:.1f}M vol; longs paying steep hourly fees.",
                })

            # --- Group C: Bearish Breakdown Shorts Candidates ---
            # Suppress altcoin breakdown shorts during macro Bull Momentum Expansion
            if regime_info.regime == "BULL_MOMENTUM_EXPANSION":
                breakdown_limit = 0
            elif regime_info.regime == "BEAR_MARKET_FLUSH":
                breakdown_limit = 8
            else:
                breakdown_limit = 1 if is_bull_regime else 3
            breakdown_filter = (pl.col("change_24h") < -1.5) & (pl.col("vol_24h") > 1_000_000)
            breakdowns_df = df.filter(breakdown_filter).sort("change_24h", descending=False).head(breakdown_limit)
            breakdown_candidates = []
            for row in breakdowns_df.iter_rows(named=True):
                c = row["coin"]
                px = float(row["price"])
                funding_apr = float(row["funding_apr"])
                chg = float(row["change_24h"])
                c_sl, c_tp = get_coin_sl_tp(c, px, is_long=False)
                breakdown_candidates.append({
                    "coin": c,
                    "bias": "SHORT",
                    "conviction_score": min(94, int(82 + abs(chg))),
                    "strategy": "SMC Trend Breakdown",
                    "mark_price": px,
                    "target_entry": round(px * 1.006, 4),
                    "stop_loss": c_sl,
                    "take_profit": c_tp,
                    "funding_apr_pct": funding_apr,
                    "volume_24h_usd": float(row["vol_24h"]),
                    "rationale": f"Bear market breakdown: {chg:+.1f}% 24h drop on ${row['vol_24h']/1e6:.1f}M vol; heavy distribution.",
                })

            # --- Hit-and-Run Group 0: Volume Burst & Momentum Acceleration ---
            # Coins bursting with volume (> $5M) and positive 24h acceleration (> 0.5%)
            burst_filter = (
                (pl.col("vol_24h") > 5_000_000)
                & (pl.col("change_24h") > 0.5)
            )
            burst_df = df.filter(burst_filter).sort("change_24h", descending=True).head(5)
            burst_candidates = []
            for row in burst_df.iter_rows(named=True):
                c = row["coin"]
                px = float(row["price"])
                funding_apr = float(row["funding_apr"])
                chg = float(row["change_24h"])
                c_sl, c_tp = get_coin_sl_tp(c, px, is_long=True)
                burst_candidates.append({
                    "coin": c,
                    "bias": "LONG",
                    "conviction_score": min(95, int(84 + chg)),
                    "strategy": "Volume Burst Velocity",
                    "mark_price": px,
                    "target_entry": round(px * 0.998, 4),
                    "stop_loss": c_sl,
                    "take_profit": c_tp,
                    "funding_apr_pct": funding_apr,
                    "volume_24h_usd": float(row["vol_24h"]),
                    "rationale": f"Volume burst: ${row['vol_24h']/1e6:.1f}M vol with +{chg:.1f}% 24h gain; rapid momentum injection.",
                })

            # Assemble prospects list in regime-aligned priority order (hit-and-run momentum & squeezes first)
            if is_bull_regime:
                priority_groups = [squeeze_candidates, burst_candidates, breakout_candidates, long_candidates, fade_short_candidates, breakdown_candidates]
            elif regime_info.regime == "BEAR_MARKET_FLUSH":
                priority_groups = [breakdown_candidates, fade_short_candidates, squeeze_candidates, burst_candidates, breakout_candidates, long_candidates]
            else:
                priority_groups = [squeeze_candidates, burst_candidates, breakout_candidates, long_candidates, fade_short_candidates, breakdown_candidates]

            for grp in priority_groups:
                for p in grp:
                    if p["coin"] not in selected_coins:
                        selected_coins.add(p["coin"])
                        prospects_list.append(p)

            # Fill remaining slots up to 10 with highest volume leaders
            if len(prospects_list) < 10:
                for row in df.iter_rows(named=True):
                    c = row["coin"]
                    if c not in selected_coins:
                        selected_coins.add(c)
                        px = float(row["price"])
                        funding_apr = float(row["funding_apr"])
                        chg = float(row["change_24h"])
                        if regime_info.regime == "BEAR_MARKET_FLUSH":
                            bias = "SHORT"  # Strict short alignment in bear flush
                        elif is_bull_regime:
                            bias = "LONG"   # Strict long alignment in bull expansion
                        else:
                            bias = "SHORT" if funding_apr > 30.0 or chg < -1.0 else "LONG"
                        c_sl, c_tp = get_coin_sl_tp(c, px, is_long=(bias == "LONG"))
                        prospects_list.append({
                            "coin": c,
                            "bias": bias,
                            "conviction_score": 80,
                            "strategy": "VWAP / Volume Breakout",
                            "mark_price": px,
                            "target_entry": round(px * (0.995 if bias == "LONG" else 1.005), 4),
                            "stop_loss": c_sl,
                            "take_profit": c_tp,
                            "funding_apr_pct": funding_apr,
                            "volume_24h_usd": float(row["vol_24h"]),
                            "rationale": f"Top volume leader (${row['vol_24h']/1e6:.1f}M 24h vol) with {bias} bias and +{funding_apr:.1f}% APR funding.",
                        })
                        if len(prospects_list) >= 10:
                            break

            # Optional Groq LPU News & Catalyst Grounding enhancement (throttled to once every 15 minutes)
            if (now_ts - getattr(self, "_last_deep_enrichment_ts", 0.0)) >= 900.0:
                self._last_deep_enrichment_ts = now_ts
                try:
                    from src.scanner.ai_studio_prospector import AIStudioProspector
                    ai_prospector = AIStudioProspector()
                    if ai_prospector.is_available():
                        self.log("🧠 [Groq LPU Prospector] Auditing top prospects via Groq LPU...", "INFO")
                        prospects_list = ai_prospector.enhance_prospects(prospects_list, use_search=True)
                except Exception as ai_p_err:
                    self.log(f"Groq LPU prospector pass-through: {ai_p_err}", "DEBUG")

            # Update in-memory prospects map
            self.prospects = {p["coin"]: p for p in prospects_list}
            biases = {p["coin"]: p["bias"] for p in prospects_list}
            self.guard.set_prospect_biases(biases)
            self.guard.set_market_regime(regime_info.regime, applied_tweaks)

            # Feed live funding rates directly into funding strategy actor
            if hasattr(self, "funding_strat") and self.funding_strat:
                for row in df.iter_rows(named=True):
                    c = row["coin"]
                    apr = float(row["funding_apr"])
                    instr_str = f"{c}-USD-PERP.HYPERLIQUID"
                    self.funding_strat.update_funding_rate(instr_str, apr)

            # Sync to bridge/prospects.json
            payload = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "scan_iteration": self._prospector_scan_count,
                "interval_minutes": int(self.prospector_interval / 60),
                "next_scan_at": datetime.fromtimestamp(self._next_prospector_scan, timezone.utc).isoformat(),
                "macro_sentiment": regime_info.sentiment,
                "regime": regime_info.regime,
                "regime_tweaks": applied_tweaks,
                "prospects": prospects_list,
            }
            with open(self.prospects_path, "w") as f:
                json.dump(payload, f, indent=2)

            # 3. Scan Delta-Neutral Funding Carry Arbitrage opportunities (throttled to once every 300 seconds)
            if (now_ts - getattr(self, "_last_arb_scan_ts", 0.0)) >= 300.0:
                self._last_arb_scan_ts = now_ts
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

            self.log(
                f"🎯 [AI Prospector] Scan #{self._prospector_scan_count} complete: updated {len(prospects_list)} targets: "
                f"{[p['coin'] for p in prospects_list]}. Next scan scheduled in {int(self.prospector_interval / 60)} minutes.",
                "INFO",
            )

            # 4. Proactively execute top high-conviction prospects (Conviction >= 88)
            # In Bull regimes, prioritize trend longs; otherwise prioritize proven Funding Fade
            def prospect_priority_key(x):
                if is_bull_regime:
                    is_bull_match = 1 if (x.get("bias") or x.get("side", "")).upper() == "LONG" else 0
                    return (is_bull_match, x.get("conviction_score", 0))
                else:
                    is_funding = 1 if "Funding" in x.get("strategy", "") else 0
                    return (is_funding, x.get("conviction_score", 0))

            for p in sorted(prospects_list, key=prospect_priority_key, reverse=True):
                p_strat = p.get("strategy", "")
                p_coin = p.get("coin", "")

                # Regime Gate: Skip TrendContinuation in Choppy range
                if regime_info.regime == "CHOPPY_MEAN_REVERTING_RANGE" and ("Trend" in p_strat or p_strat == "TrendContinuationSMC"):
                    self.log(f"🛡️ [Regime Shield] Skipped prospect {p_coin} ({p_strat}): TrendContinuation blocked in CHOPPY_MEAN_REVERTING_RANGE", "INFO")
                    continue

                # Probation Status Check: If in probation, candidate executes under probation sizing (0.4x)
                if hasattr(self, "guard") and self.guard and self.guard.is_strategy_locked(p_strat):
                    self.log(f"⚠️ [Strategy Probation] Candidate {p_coin} ({p_strat}): Strategy in probation; sizing scaled down to 0.4x", "INFO")

                score = p.get("conviction_score", 0)
                open_pos_now = self.get_open_positions()
                self.guard.sync_open_positions(open_pos_now)
                open_and_pending = len(open_pos_now) + len(getattr(self.guard, "pending_orders", {}))
                self.log(f"📋 [Prospect Eval] Candidate {p_coin}: Score={score}, Open={len(open_pos_now)}, Pending={len(getattr(self.guard, 'pending_orders', {}))}", "INFO")
                if score >= 75:
                    if open_and_pending >= self.guard.max_total_open_positions:
                        # Only consider capital rotation if candidate prospect has exceptional conviction (>= 92)
                        # and existing position has had at least 45 minutes to develop towards TP/SL
                        if p.get("conviction_score", 0) >= 92:
                            rotation_cand = self.find_capital_rotation_candidate(p.get("coin", ""), conviction_score=p.get("conviction_score", 0))
                            if rotation_cand:
                                coin_to_close, rot_reason = rotation_cand
                                self.log(
                                    f"🔄 [Capital Rotation] Closing stagnant position {coin_to_close} to rotate capital into {p.get('coin', '')} ({rot_reason})",
                                    "TRIGGER",
                                )
                                self.close_position(coin_to_close, reason=rot_reason)
                                time.sleep(0.5)
                        open_and_pending = len(self.get_open_positions()) + len(getattr(self.guard, "pending_orders", {}))
                        if open_and_pending >= self.guard.max_total_open_positions:
                            continue
                    self.execute_prospect_entry(p)

        except Exception as e:
            err_str = str(e)
            self.log(f"Prospector scan warning: {err_str}", "WARN")
            if hasattr(self, "fallback_engine") and self.fallback_engine:
                self.fallback_engine.trigger_fallback(err_str)
                self.log(f"🛡️ [AI Fallback] Activated: generating deterministic rule-based quantitative prospects ({err_str}).", "TRIGGER")
                try:
                    det_prospects = self.fallback_engine.generate_deterministic_prospects(self.info_client)
                    if det_prospects:
                        self.prospects = det_prospects
                        self.guard.set_prospect_biases({c: p.get("bias", "NEUTRAL") for c, p in det_prospects.items()})
                        self.log(f"🛡️ [AI Fallback] Successfully set {len(det_prospects)} deterministic rule-based prospects.", "INFO")
                except Exception as fb_err:
                    self.log(f"Fallback generation error: {fb_err}", "ERROR")
            self._update_ai_status(open_count=len(self.get_open_positions()))

    def _ai_prospector_loop(self) -> None:
        """In-process market prospector scanning top 50 markets every 15m."""
        self.log(f"🔍 [AI Prospector] High-frequency scanner started ({int(self.prospector_interval / 60)}m cycle).", "INFO")
        # Wait up to 10s for node and strategies to complete on_start()
        for _ in range(20):
            if not self._is_running:
                return
            if (
                (self.continuation_strat and hasattr(self.continuation_strat, "instruments_map") and len(self.continuation_strat.instruments_map) > 0)
                or (self.momentum_strat and hasattr(self.momentum_strat, "instruments_map") and len(self.momentum_strat.instruments_map) > 0)
            ):
                break
            time.sleep(0.5)

        while self._is_running:
            self._run_prospector_scan()
            for _ in range(int(self.prospector_interval)):
                if not self._is_running or self._force_prospector_scan:
                    self._force_prospector_scan = False
                    break
                time.sleep(1.0)

    def _sync_bridge_state_loop(self) -> None:
        """
        Maintain fast in-memory state dictionary and throttle disk persistence
        to once every 5.0 seconds (atomic write with .tmp -> os.replace).
        """
        last_disk_sync = 0.0
        while self._is_running:
            try:
                state = self.get_state()
                self._latest_state = state
                self._sweep_protection_orders()

                now = time.time()
                if (now - last_disk_sync) >= 5.0:
                    last_disk_sync = now

                    # 1. Update bridge/active_trades.json atomically (.tmp -> os.replace)
                    tmp_act = f"{self.active_trades_path}.tmp"
                    with open(tmp_act, "w") as f:
                        json.dump({
                            "timestamp": state["timestamp"],
                            "equity": state["equity"],
                            "cash_balance": state["cash_balance"],
                            "positions": state["positions"],
                        }, f, indent=2)
                    os.replace(tmp_act, self.active_trades_path)

                    # 2. Persist paper state atomically
                    if self.paper:
                        realized_pnl = self._get_net_realized_pnl()
                        self._paper_realized_pnl = realized_pnl
                        self.save_paper_state(
                            equity=state["equity"],
                            realized_pnl=realized_pnl,
                            positions=state["positions"],
                        )
            except Exception as e:
                self.log(f"Bridge state sync loop warning: {e}", "DEBUG")
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
        if hasattr(self, "analytics_pipeline") and self.analytics_pipeline:
            self.analytics_pipeline.stop()

        # Persist final state
        try:
            state = self.get_state()
            realized_pnl = self._get_net_realized_pnl() if self.paper else (state["cash_balance"] - self._paper_starting_balance)
            self.save_paper_state(state["equity"], realized_pnl, state["positions"])
        except Exception as state_err:
            self.log(f"Failed to persist final state during stop: {state_err}", "DEBUG")

        if self.node:
            try:
                self.node.stop()
                self.node.dispose()
            except Exception as node_err:
                self.log(f"Error stopping/disposing TradingNode: {node_err}", "DEBUG")

        self.log("TradingNode disposed cleanly. Shutdown complete.", "INFO")
