"""
Strategy 5: DeltaNeutralCarryStrategy (funding_arbitrage.py)
Automated Delta-Neutral Funding Carry Arbitrage Strategy for Hyperliquid.

Core Mechanics:
1. Market Surveillance & Pair Discovery:
   - Periodically queries detected funding carry arbitrage pairs via bridge/funding_arbitrage.json
     or ArbitrageScanner.find_arbitrage_pairs().
   - Minimum Entry Threshold: Combined Net Carry APR >= min_net_carry_apr (default: 80.0%).
2. Microstructure Pre-Filtering:
   - For every candidate pair:
     * Bid-ask spread <= max_spread_pct (default: 0.10%).
     * Top-5 order book depth >= min_top5_depth_usd (default: $10,000 USD).
     * Eliminates toxic, illiquid, and high-slippage markets.
3. Delta-Neutral Sizing & Concurrent Execution:
   - Long Leg: Token with negative funding (shorts pay longs -> long receives carry).
   - Short Leg: Token with positive funding (longs pay shorts -> short receives carry).
   - Both legs sized to equal USD notional (leg_capital_usd, default: $10.0 USD) to ensure
     net market delta ~ 0.0.
   - Concurrently submits both orders and tracks paired position IDs.
4. Dynamic Carry Compression Exit:
   - Monitors live funding rates for active paired positions.
   - When net carry compresses below exit_net_carry_apr (default: 30.0%) or on circuit breaker trigger,
     unwinds both legs simultaneously.
5. Risk & PortfolioGuard Integration:
   - Enforces per-strategy margin allocation, coin max leverage, and drawdown stops.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import os
import sys
import time
import datetime
from pathlib import Path

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.utils.instruments import get_coin_max_leverage
from src.scanner.mcp_client import HyperliquidInfoClient
from src.scanner.arbitrage_scanner import ArbitrageScanner


@dataclass
class PairedCarryPosition:
    """Tracks an active paired delta-neutral carry trade."""
    pair_id: str
    long_instrument_id: InstrumentId
    short_instrument_id: InstrumentId
    long_coin: str
    short_coin: str
    long_notional_usd: float
    short_notional_usd: float
    long_entry_price: float
    short_entry_price: float
    entry_net_carry_apr: float
    current_net_carry_apr: float
    entry_time: float
    long_order_id: Optional[Any] = None
    short_order_id: Optional[Any] = None
    status: str = "OPEN"  # "OPEN", "CLOSING", "CLOSED"


class DeltaNeutralCarryConfig(StrategyConfig, kw_only=True):
    """Configuration for DeltaNeutralCarryStrategy."""
    min_net_carry_apr: float = 80.0         # Minimum combined APR to enter pair (80.0%)
    exit_net_carry_apr: float = 30.0        # Exit when net carry compresses below 30.0%
    max_spread_pct: float = 0.10            # Max allowed bid-ask spread per leg (0.10%)
    min_top5_depth_usd: float = 10000.0     # Minimum top-5 book depth ($10,000 USD)
    leg_capital_usd: float = 10.0           # Size per leg in USD ($10 USD)
    max_active_pairs: int = 2               # Maximum concurrent active pairs
    venue: str = "HYPERLIQUID"
    bridge_path: Optional[str] = None
    check_interval_seconds: float = 60.0    # Scan interval


class DeltaNeutralCarryStrategy(Strategy):
    """
    NautilusTrader implementation of Delta-Neutral Funding Carry Arbitrage Strategy.
    """

    def __init__(
        self,
        config: DeltaNeutralCarryConfig,
        portfolio_guard: Optional[Any] = None,
        info_client: Optional[Any] = None,
        arbitrage_scanner: Optional[Any] = None,
    ) -> None:
        super().__init__(config)
        self.carry_config: DeltaNeutralCarryConfig = config
        self.venue = Venue(config.venue)
        self.portfolio_guard = portfolio_guard
        self.info_client = info_client or HyperliquidInfoClient()
        self.arbitrage_scanner = arbitrage_scanner or ArbitrageScanner(
            info_client=self.info_client,
            bridge_path=config.bridge_path,
        )

        self.instruments_map: Dict[str, Instrument] = {}
        self.active_pairs: Dict[str, PairedCarryPosition] = {}
        self.last_scan_time: float = 0.0

    def on_start(self) -> None:
        """Register instruments and initialize surveillance."""
        instruments = [i for i in self.cache.instruments() if str(i.id.venue) == self.carry_config.venue]
        self.log.info(f"DeltaNeutralCarryStrategy active across {len(instruments)} instruments on {self.venue}")

        for instrument in instruments:
            instr_str = str(instrument.id)
            self.instruments_map[instr_str] = instrument
            # Also index by base symbol prefix (e.g. 'BTC')
            coin = str(instrument.id.symbol).split("-")[0].split(".")[0].upper()
            self.instruments_map[coin] = instrument

            # Subscribe to 5M bars for regular execution loop
            try:
                self.subscribe_bars(BarType.from_str(f"{instr_str}-5-MINUTE-LAST-EXTERNAL"))
            except Exception:
                pass

    def on_bar(self, bar: Bar) -> None:
        """Periodic bar evaluation: monitor carry compression exits and scan new entries."""
        now = time.time()
        # Check active pair exits on every bar
        self.check_active_pairs_exit()

        # Throttle new pair scanning by check_interval_seconds
        if now - self.last_scan_time >= self.carry_config.check_interval_seconds:
            self.check_arbitrage_opportunities()
            self.last_scan_time = now

    def check_arbitrage_opportunities(self) -> List[str]:
        """
        Query detected delta-neutral funding carry pairs, enforce microstructure pre-filters,
        and enter qualified pairs with delta-neutral equal-dollar sizing.

        Returns
        -------
        list[str]
            IDs of newly entered pairs.
        """
        entered_pair_ids: List[str] = []

        if len(self.active_pairs) >= self.carry_config.max_active_pairs:
            return entered_pair_ids

        # 1. Query candidate pairs from bridge or scanner
        try:
            candidates = self.arbitrage_scanner.find_arbitrage_pairs(
                min_net_carry_apr=self.carry_config.min_net_carry_apr,
                max_spread_pct=self.carry_config.max_spread_pct,
                min_top5_depth_usd=self.carry_config.min_top5_depth_usd,
                use_cache=True,
            )
        except Exception as e:
            self.log.warning(f"Failed to query arbitrage pairs: {e}")
            candidates = []

        if not candidates:
            return entered_pair_ids

        for pair_data in candidates:
            if len(self.active_pairs) >= self.carry_config.max_active_pairs:
                break

            pair_id = pair_data.get("pair_id")
            if not pair_id or pair_id in self.active_pairs:
                continue

            # Minimum net carry threshold verification
            net_carry = float(pair_data.get("net_carry_apr_pct", 0.0))
            min_thresh = self.carry_config.min_net_carry_apr
            min_thresh_pct = min_thresh if min_thresh > 1.0 else min_thresh * 100.0
            if net_carry < min_thresh_pct:
                continue

            long_leg = pair_data.get("long_leg", {})
            short_leg = pair_data.get("short_leg", {})
            long_coin = long_leg.get("coin", "").upper()
            short_coin = short_leg.get("coin", "").upper()

            if not long_coin or not short_coin:
                continue

            # Check if any leg is already active in existing pairs
            already_active = any(
                long_coin in (p.long_coin, p.short_coin) or short_coin in (p.long_coin, p.short_coin)
                for p in self.active_pairs.values()
            )
            if already_active:
                continue

            # Locate Nautilus Instruments
            long_instrument = self._resolve_instrument(long_coin)
            short_instrument = self._resolve_instrument(short_coin)
            if not long_instrument or not short_instrument:
                self.log.debug(f"Instruments not found in cache for pair {pair_id} ({long_coin}, {short_coin})")
                continue

            # Microstructure Pre-Filter Verification
            long_micro = self.arbitrage_scanner.evaluate_liquidity_and_spread(
                coin=long_coin,
                max_spread_pct=self.carry_config.max_spread_pct,
                min_top5_depth_usd=self.carry_config.min_top5_depth_usd,
            )
            short_micro = self.arbitrage_scanner.evaluate_liquidity_and_spread(
                coin=short_coin,
                max_spread_pct=self.carry_config.max_spread_pct,
                min_top5_depth_usd=self.carry_config.min_top5_depth_usd,
            )

            if not long_micro["passed"] or not short_micro["passed"]:
                fail_reasons = []
                if not long_micro["passed"]:
                    fail_reasons.append(f"Long {long_coin}: {long_micro['reason']}")
                if not short_micro["passed"]:
                    fail_reasons.append(f"Short {short_coin}: {short_micro['reason']}")
                self.log.info(f"Skipping pair {pair_id} - pre-filter rejection: {'; '.join(fail_reasons)}")
                continue

            # Attempt paired entry
            success = self._enter_paired_carry(
                long_instrument=long_instrument,
                short_instrument=short_instrument,
                pair_data=pair_data,
                long_micro=long_micro,
                short_micro=short_micro,
            )
            if success:
                entered_pair_ids.append(pair_id)

        return entered_pair_ids

    def _enter_paired_carry(
        self,
        long_instrument: Instrument,
        short_instrument: Instrument,
        pair_data: Dict[str, Any],
        long_micro: Optional[Dict[str, Any]] = None,
        short_micro: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Executes concurrent delta-neutral paired entry with equal dollar notionals.
        """
        pair_id = pair_data.get("pair_id", f"{long_instrument.id}_{short_instrument.id}")
        target_leg_usd = float(self.carry_config.leg_capital_usd)

        # 1. Price Resolution
        long_px = (long_micro or {}).get("best_ask") or float(pair_data.get("long_leg", {}).get("oracle_price", 0.0))
        short_px = (short_micro or {}).get("best_bid") or float(pair_data.get("short_leg", {}).get("oracle_price", 0.0))

        if long_px <= 0 or short_px <= 0:
            self.log.warning(f"Invalid prices for pair {pair_id}: long_px={long_px}, short_px={short_px}")
            return False

        # 2. Equal Dollar Notional Sizing (Delta Neutral)
        long_qty_val = target_leg_usd / long_px
        short_qty_val = target_leg_usd / short_px

        long_quantity = long_instrument.make_qty(Decimal(str(round(long_qty_val, long_instrument.size_precision))))
        short_quantity = short_instrument.make_qty(Decimal(str(round(short_qty_val, short_instrument.size_precision))))

        if long_quantity.as_double() <= 0 or short_quantity.as_double() <= 0:
            self.log.warning(f"Zero quantity computed for pair {pair_id}: long_qty={long_quantity}, short_qty={short_quantity}")
            return False

        long_notional = float(long_quantity.as_double()) * long_px
        short_notional = float(short_quantity.as_double()) * short_px

        # 3. PortfolioGuard Risk Validation
        if self.portfolio_guard is not None:
            equity = self._get_account_equity()
            self.portfolio_guard.update_equity(equity)

            # Check Long Leg
            open_count = self.cache.positions_open_count() if self.cache else len(self.active_pairs) * 2
            can_long, r_long = self.portfolio_guard.can_open_position(
                strategy_name=self.__class__.__name__,
                instrument_id=long_instrument.id,
                side=OrderSide.BUY,
                proposed_notional_usd=long_notional,
                current_open_positions_count=open_count,
            )
            if not can_long:
                self.log.warning(f"PortfolioGuard blocked Long leg of {pair_id}: {r_long}")
                return False

            # Check Short Leg (accounting for the pending long leg)
            can_short, r_short = self.portfolio_guard.can_open_position(
                strategy_name=self.__class__.__name__,
                instrument_id=short_instrument.id,
                side=OrderSide.SELL,
                proposed_notional_usd=short_notional,
                current_open_positions_count=open_count + 1,
            )
            if not can_short:
                self.log.warning(f"PortfolioGuard blocked Short leg of {pair_id}: {r_short}")
                return False

        # 4. Submit Concurrent Orders
        net_carry_pct = float(pair_data.get("net_carry_apr_pct", 0.0))
        self.log.info(
            f"⚡ [Carry Arb] ENTERING PAIR {pair_id} | Net Carry: +{net_carry_pct:.1f}% APR\n"
            f"   → Leg 1 (LONG):  {long_instrument.id} Qty={long_quantity} (~${long_notional:.2f}) @ ${long_px:,.4f}\n"
            f"   → Leg 2 (SHORT): {short_instrument.id} Qty={short_quantity} (~${short_notional:.2f}) @ ${short_px:,.4f}"
        )

        try:
            # Create orders
            factory = self.order_factory if self.order_factory is not None else getattr(self, "_order_factory", None)
            if factory is None:
                self.log.error(f"Cannot submit carry orders: order_factory is not initialized.")
                return False

            long_order = factory.market(
                instrument_id=long_instrument.id,
                order_side=OrderSide.BUY,
                quantity=long_quantity,
                time_in_force=TimeInForce.IOC,
            )
            short_order = factory.market(
                instrument_id=short_instrument.id,
                order_side=OrderSide.SELL,
                quantity=short_quantity,
                time_in_force=TimeInForce.IOC,
            )

            # Submit orders concurrently
            self.submit_order(long_order)
            self.submit_order(short_order)

            # Register with PortfolioGuard
            if self.portfolio_guard is not None:
                self.portfolio_guard.register_order_submitted(
                    order=long_order,
                    strategy_name=self.__class__.__name__,
                    notional_usd=long_notional,
                )
                self.portfolio_guard.register_order_submitted(
                    order=short_order,
                    strategy_name=self.__class__.__name__,
                    notional_usd=short_notional,
                )

            # Track active pair
            self.active_pairs[pair_id] = PairedCarryPosition(
                pair_id=pair_id,
                long_instrument_id=long_instrument.id,
                short_instrument_id=short_instrument.id,
                long_coin=pair_data.get("long_leg", {}).get("coin", str(long_instrument.id)),
                short_coin=pair_data.get("short_leg", {}).get("coin", str(short_instrument.id)),
                long_notional_usd=long_notional,
                short_notional_usd=short_notional,
                long_entry_price=long_px,
                short_entry_price=short_px,
                entry_net_carry_apr=net_carry_pct,
                current_net_carry_apr=net_carry_pct,
                entry_time=time.time(),
                long_order_id=long_order.id,
                short_order_id=short_order.id,
                status="OPEN",
            )
            return True

        except Exception as e:
            self.log.error(f"Failed to submit paired carry orders for {pair_id}: {e}")
            return False

    def check_active_pairs_exit(self) -> List[str]:
        """
        Audit all active paired carry positions.
        Exits both legs simultaneously if:
        1. Net carry compresses below exit_net_carry_apr (default: 30.0%).
        2. PortfolioGuard circuit breaker is triggered.

        Returns
        -------
        list[str]
            IDs of exited pairs.
        """
        exited_pair_ids: List[str] = []

        # Circuit breaker emergency exit
        circuit_breaker = False
        if self.portfolio_guard and getattr(self.portfolio_guard, "is_circuit_breaker_triggered", False):
            circuit_breaker = True

        for pair_id, paired_pos in list(self.active_pairs.items()):
            if paired_pos.status != "OPEN":
                continue

            if circuit_breaker:
                self._exit_paired_carry(paired_pos, "PortfolioGuard Circuit Breaker Triggered")
                exited_pair_ids.append(pair_id)
                continue

            # Fetch live funding rates for each leg
            long_apr = self._get_coin_funding_apr(paired_pos.long_coin)
            short_apr = self._get_coin_funding_apr(paired_pos.short_coin)

            # Net Carry APR = short_apr - long_apr
            cur_net_carry_apr_pct = (short_apr - long_apr) * 100.0
            paired_pos.current_net_carry_apr = cur_net_carry_apr_pct

            exit_thresh = self.carry_config.exit_net_carry_apr
            exit_thresh_pct = exit_thresh if exit_thresh > 1.0 else exit_thresh * 100.0

            if cur_net_carry_apr_pct < exit_thresh_pct:
                reason = (
                    f"Net carry compressed to {cur_net_carry_apr_pct:.1f}% APR "
                    f"(below exit threshold {exit_thresh_pct:.1f}% APR)"
                )
                self._exit_paired_carry(paired_pos, reason)
                exited_pair_ids.append(pair_id)

        return exited_pair_ids

    def _exit_paired_carry(self, paired_pos: PairedCarryPosition, reason: str) -> None:
        """
        Simultaneously closes both legs of an active paired carry position.
        """
        paired_pos.status = "CLOSING"
        self.log.info(
            f"🚪 [Carry Arb] EXITING PAIR {paired_pos.pair_id}: Reason='{reason}' "
            f"(Entry Carry: +{paired_pos.entry_net_carry_apr:.1f}%, Current Carry: +{paired_pos.current_net_carry_apr:.1f}%)"
        )

        try:
            self.close_all_positions(paired_pos.long_instrument_id)
        except Exception as e:
            self.log.error(f"Failed to close Long leg for {paired_pos.pair_id}: {e}")

        try:
            self.close_all_positions(paired_pos.short_instrument_id)
        except Exception as e:
            self.log.error(f"Failed to close Short leg for {paired_pos.pair_id}: {e}")

        # Remove from active tracking
        self.active_pairs.pop(paired_pos.pair_id, None)

    def on_position_closed(self, event: Any) -> None:
        """Release margin in PortfolioGuard on position close."""
        if self.portfolio_guard is not None:
            pos = getattr(event, "position", event)
            qty = getattr(pos, "peak_qty", getattr(pos, "quantity", 0.0))
            px = getattr(pos, "avg_px_open", 0.0)
            q_val = float(qty.as_double()) if hasattr(qty, "as_double") else float(qty or 0.0)
            p_val = float(px.as_double()) if hasattr(px, "as_double") else float(px or 0.0)
            freed = abs(q_val * p_val)
            instr_id = getattr(event, "instrument_id", getattr(pos, "instrument_id", None))
            if instr_id:
                self.portfolio_guard.register_position_closed(
                    strategy_name=self.__class__.__name__,
                    instrument_id=instr_id,
                    freed_notional_usd=freed,
                )

    def _resolve_instrument(self, coin: str, prefer_spot: bool = False) -> Optional[Instrument]:
        """Resolve coin name to Instrument in cache with spot vs perp resolution."""
        coin_clean = coin.upper().split("-")[0].split(".")[0]
        spot_aliases = [coin_clean, f"U{coin_clean}", f"{coin_clean}/USDC", f"{coin_clean}-SPOT"]

        if prefer_spot:
            for alias in spot_aliases:
                for key, instr in self.instruments_map.items():
                    if alias in key and ("SPOT" in key or "/" in key or key.startswith("U")):
                        return instr
            try:
                for instr in self.cache.instruments():
                    sym = str(instr.id.symbol)
                    if any(alias in sym for alias in spot_aliases) and ("SPOT" in sym or "/" in sym or sym.startswith("U")):
                        return instr
            except Exception:
                pass

        if coin_clean in self.instruments_map:
            return self.instruments_map[coin_clean]
        for key, instr in self.instruments_map.items():
            if key.startswith(coin_clean):
                return instr
        # Try finding in cache
        try:
            for instr in self.cache.instruments():
                if str(instr.id.symbol).startswith(coin_clean):
                    self.instruments_map[coin_clean] = instr
                    return instr
        except Exception:
            pass
        return None


    def _get_coin_funding_apr(self, coin: str) -> float:
        """Query current annualized funding rate APR for coin."""
        try:
            rate = self.info_client.get_funding_rate(coin)
            if rate is not None:
                return float(rate)
        except Exception:
            pass
        return 0.0

    def _get_account_equity(self) -> float:
        """Retrieve total account equity from cache or default."""
        try:
            account = self.portfolio.account(self.venue)
            if account is not None:
                bal = account.balance_total(USD)
                if bal is not None:
                    return float(bal.as_double())
        except Exception:
            pass
        return 100.0
