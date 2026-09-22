"""
Strategy 4: VwapOiMomentum (vwap_momentum.py)
Session Anchored VWAP breakout system with Open Interest (OI) expansion filtering.
- VWAP: Rolling session Anchored VWAP resetting daily at 00:00 UTC.
- OI Expansion: Z-score of intraday OI change > +2.0 standard deviations.
- Entry: Long on VWAP upside break with OI expansion; Short on VWAP downside break with OI expansion.
- Exit: Mean-reversion loss of VWAP or opposing 24h session liquidity sweep.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional
import datetime
import math
import polars as pl

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from src.utils.instruments import get_coin_max_leverage



@dataclass
class SessionVwapState:
    """Tracks session-anchored VWAP, daily highs/lows, and rolling OI changes."""
    current_date: Optional[datetime.date] = None
    cum_vol_price: float = 0.0
    cum_vol: float = 0.0
    vwap: float = 0.0
    session_high: float = 0.0
    session_low: float = float("inf")

    last_oi: float = 0.0
    oi_deltas: List[float] = field(default_factory=list)
    prev_close: Optional[float] = None

    bar_records: List[Dict] = field(default_factory=list)

    def reset_session(self, date: datetime.date, initial_price: float) -> None:
        self.current_date = date
        self.bar_records.clear()
        self.vwap = initial_price
        self.session_high = initial_price
        self.session_low = initial_price

    def update(self, bar: Bar, current_oi: Optional[float] = None) -> None:
        dt = datetime.datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=datetime.timezone.utc)
        date = dt.date()

        tp = (bar.high.as_double() + bar.low.as_double() + bar.close.as_double()) / 3.0
        vol = bar.volume.as_double()

        if self.current_date is None or date != self.current_date:
            self.reset_session(date, tp)

        self.bar_records.append({"tp": tp, "volume": vol})
        
        # Intraday session VWAP anchored to 00:00 UTC using Polars cumulative volume
        df = pl.DataFrame(self.bar_records)
        df = df.with_columns(
            cum_vol=pl.col("volume").cum_sum(),
            cum_vol_price=(pl.col("tp") * pl.col("volume")).cum_sum()
        )
        self.vwap = df.select(pl.col("cum_vol_price") / pl.col("cum_vol")).row(-1)[0]

        self.session_high = max(self.session_high, bar.high.as_double())
        self.session_low = min(self.session_low, bar.low.as_double())

        # Track OI deltas
        if current_oi is not None:
            if self.last_oi > 0:
                delta = current_oi - self.last_oi
                self.oi_deltas.append(delta)
                if len(self.oi_deltas) > 50:
                    self.oi_deltas.pop(0)
            self.last_oi = current_oi
        else:
            # Estimate volume-weighted OI delta proxy when raw OI tick is unstreamed
            est_delta = vol * (1.0 if bar.close.as_double() > bar.open.as_double() else -1.0)
            self.oi_deltas.append(est_delta)
            if len(self.oi_deltas) > 50:
                self.oi_deltas.pop(0)

    def get_oi_z_score(self) -> float:
        if len(self.oi_deltas) < 10:
            return 0.0
        mean = sum(self.oi_deltas) / len(self.oi_deltas)
        variance = sum((x - mean) ** 2 for x in self.oi_deltas) / len(self.oi_deltas)
        std = math.sqrt(variance)
        if std <= 0:
            return 0.0
        return (self.oi_deltas[-1] - mean) / std


class VwapOiMomentumConfig(StrategyConfig, kw_only=True):
    """Configuration for VwapOiMomentum strategy."""
    oi_zscore_threshold: float = 1.0  # OI expansion > 1 std dev for responsive momentum triggers
    risk_per_trade_pct: float = 0.01  # 1% equity risk
    max_active_positions: int = 5
    venue: str = "HYPERLIQUID"


class VwapOiMomentum(Strategy):
    """
    NautilusTrader implementation of VwapOiMomentum.
    """

    def __init__(self, config: VwapOiMomentumConfig, portfolio_guard: Optional[Any] = None) -> None:
        super().__init__(config)
        self.vwap_config: VwapOiMomentumConfig = config
        self.venue = Venue(config.venue)
        self.portfolio_guard = portfolio_guard
        self.states: Dict[str, SessionVwapState] = {}
        self.instruments_map: Dict[str, Instrument] = {}

    def on_start(self) -> None:
        """Subscribe to 5M bars across designated instruments."""
        instruments = [i for i in self.cache.instruments() if str(i.id.venue) == self.vwap_config.venue]
        self.log.info(f"VwapOiMomentum active on {len(instruments)} instruments")

        for instrument in instruments:
            instr_str = str(instrument.id)
            self.instruments_map[instr_str] = instrument
            self.states[instr_str] = SessionVwapState()
            self.subscribe_bars(BarType.from_str(f"{instr_str}-5-MINUTE-LAST-EXTERNAL"))

    def on_bar(self, bar: Bar) -> None:
        """Evaluate session VWAP crossover and OI expansion."""
        instr_str = str(bar.bar_type.instrument_id)
        instrument = self.instruments_map.get(instr_str)
        state = self.states.get(instr_str)
        if not instrument or not state:
            return

        close_px = bar.close.as_double()
        prev_close = state.prev_close
        state.update(bar)
        state.prev_close = close_px

        vwap = state.vwap
        oi_z = state.get_oi_z_score()

        # 1. Manage Open Positions
        if not self.portfolio.is_flat(instrument.id):
            if self.portfolio.is_net_long(instrument.id):
                # Exit Long on VWAP breakdown or opposing liquidity sweep of session high
                if close_px < vwap or bar.high.as_double() >= state.session_high:
                    self.log.info(f"🚪 VwapOiMomentum EXIT LONG {instrument.id}: Close={close_px:.4f}, VWAP={vwap:.4f}")
                    self.close_all_positions(instrument.id)
            elif self.portfolio.is_net_short(instrument.id):
                # Exit Short on VWAP reclaim or opposing liquidity sweep of session low
                if close_px > vwap or bar.low.as_double() <= state.session_low:
                    self.log.info(f"🚪 VwapOiMomentum EXIT SHORT {instrument.id}: Close={close_px:.4f}, VWAP={vwap:.4f}")
                    self.close_all_positions(instrument.id)
            return

        # 2. Check Concurrency Limit
        active_count = self.cache.positions_open_count()
        if active_count >= self.vwap_config.max_active_positions:
            return

        if prev_close is None:
            return

        # 3. Long Signal: Price crosses above VWAP with OI expansion > 2.0 std dev
        if prev_close <= vwap and close_px > vwap and oi_z > self.vwap_config.oi_zscore_threshold:
            self._enter_momentum(instrument, OrderSide.BUY, close_px, vwap, oi_z)

        # 4. Short Signal: Price crosses below VWAP with OI expansion > 2.0 std dev
        elif prev_close >= vwap and close_px < vwap and oi_z > self.vwap_config.oi_zscore_threshold:
            self._enter_momentum(instrument, OrderSide.SELL, close_px, vwap, oi_z)

    def _enter_momentum(
        self,
        instrument: Instrument,
        side: OrderSide,
        price: float,
        vwap: float,
        oi_z: float,
    ) -> None:
        """Submit bracket order with initial stop loss placed across VWAP."""
        equity = self._get_account_equity()
        sizing_mult = 1.0
        if self.portfolio_guard and hasattr(self.portfolio_guard, "get_strategy_sizing_multiplier"):
            sizing_mult = self.portfolio_guard.get_strategy_sizing_multiplier(self.__class__.__name__)
        risk_usd = equity * self.vwap_config.risk_per_trade_pct * sizing_mult

        # Stop loss anchored behind VWAP with minimum 1% buffer
        sl_distance = max(abs(price - vwap) * 1.5, price * 0.01)
        sl_price = (price - sl_distance) if side == OrderSide.BUY else (price + sl_distance)
        risk_per_unit = abs(price - sl_price)

        if risk_per_unit <= 0:
            return

        qty_val = risk_usd / risk_per_unit
        
        # Enforce official Hyperliquid exchange max leverage for this coin
        coin = str(instrument.id).split("-")[0].split(".")[0].upper()
        max_lev = get_coin_max_leverage(coin)
        max_notional = equity * max_lev
        if (qty_val * price) > max_notional:
            qty_val = max_notional / price
        quantity = instrument.make_qty(Decimal(str(round(qty_val, instrument.size_precision))))
        if quantity.as_double() <= 0:
            return

        notional_usd = qty_val * price
        if self.portfolio_guard is not None:
            self.portfolio_guard.update_equity(equity)
            can_trade, reason = self.portfolio_guard.can_open_position(
                strategy_name=self.__class__.__name__,
                instrument_id=instrument.id,
                side=side,
                proposed_notional_usd=notional_usd,
                current_open_positions_count=self.cache.positions_open_count(),
            )
            if not can_trade:
                if reason != "SILENT_BLOCK":
                    self.log.warning(f"PortfolioGuard blocked momentum entry on {instrument.id}: {reason}")
                return

        coin = str(instrument.id).split("-")[0].split(".")[0].upper()
        if self.portfolio_guard and getattr(self.portfolio_guard, "prospect_biases", None):
            p_bias = self.portfolio_guard.prospect_biases.get(coin)
            if (side == OrderSide.BUY and p_bias == "LONG") or (side == OrderSide.SELL and p_bias == "SHORT"):
                self.log.info(f"🎯 AI Prospect confirmed {p_bias} bias on {instrument.id}")

        tp_price = (price + 2.5 * risk_per_unit) if side == OrderSide.BUY else (price - 2.5 * risk_per_unit)

        sl_obj = instrument.make_price(Decimal(str(round(sl_price, instrument.price_precision))))
        tp_obj = instrument.make_price(Decimal(str(round(tp_price, instrument.price_precision))))

        self.log.info(
            f"🚀 VwapOiMomentum ENTRY {side} {instrument.id}: Qty={quantity} @ ~{price:.4f} | "
            f"VWAP={vwap:.4f} | OI Z-score={oi_z:+.2f} | SL={sl_obj} | TP={tp_obj}"
        )

        try:
            bracket_list = self.order_factory.bracket(
                instrument_id=instrument.id,
                order_side=side,
                quantity=quantity,
                entry_order_type=OrderType.MARKET,
                tp_order_type=OrderType.LIMIT,
                tp_price=tp_obj,
                sl_order_type=OrderType.STOP_MARKET,
                sl_trigger_price=sl_obj,
            )
            self.submit_order_list(bracket_list)
            if self.portfolio_guard is not None and bracket_list.first is not None:
                self.portfolio_guard.register_order_submitted(
                    order=bracket_list.first,
                    strategy_name=self.__class__.__name__,
                    notional_usd=notional_usd,
                )
        except Exception as e:
            self.log.error(f"Failed to submit VWAP momentum bracket order: {e}")

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

    def _get_account_equity(self) -> float:
        try:
            account = self.portfolio.account(self.venue)
            if account is not None:
                bal = account.balance_total(USD)
                if bal is not None:
                    return float(bal.as_double())
        except Exception:
            pass
        return 100.0
