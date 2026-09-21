"""
Strategy 1: TrendContinuationSMC (continuation.py)
Multi-timeframe Smart Money Concepts (SMC) trend continuation strategy.
- 4H Regime: 50 EMA slope > 0 and price > 200 EMA (Bullish) or 50 EMA slope < 0 and price < 200 EMA (Bearish).
- 30M Level: Unmitigated Demand/Supply zones formed by high-volume displacement (|close - open| > 1.5 * ATR14).
- 5M Trigger: Market Structure Shift (MSS) candle close breaking the previous 5M swing fractal upon zone touch.
- Execution: 1% equity risk bracket order with STOP_MARKET (isTrigger=True) below 5M swing wick and 2.5:1 R:R limit TP.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Any
import polars as pl
from smartmoneyconcepts import smc

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import AverageTrueRange, ExponentialMovingAverage
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from src.utils.instruments import get_coin_max_leverage



@dataclass
class Zone:
    """Represents an unmitigated 30M Demand or Supply zone."""
    zone_type: str  # "DEMAND" or "SUPPLY"
    low: float
    high: float
    ts_event: int
    mitigated: bool = False
    touched: bool = False


@dataclass
class SMCInstrumentState:
    """State tracker for SMC trend, zones, and 5M market structure."""
    instrument_id: InstrumentId
    ema_50_4h: ExponentialMovingAverage
    ema_200_4h: ExponentialMovingAverage
    prev_ema_50: Optional[float] = None
    ema_50_slope: float = 0.0

    atr_30m: AverageTrueRange = field(default_factory=lambda: AverageTrueRange(14))
    atr_5m: AverageTrueRange = field(default_factory=lambda: AverageTrueRange(14))

    # Trend State: "BULLISH", "BEARISH", "NEUTRAL"
    trend_state: str = "NEUTRAL"

    # Zone registry
    demand_zones: List[Zone] = field(default_factory=list)
    supply_zones: List[Zone] = field(default_factory=list)

    # 5M Market Structure Tracking
    recent_5m_bars: List[Bar] = field(default_factory=list)
    recent_30m_bars: List[Bar] = field(default_factory=list)
    recent_swing_high: Optional[float] = None
    recent_swing_low: Optional[float] = None
    recent_swing_wick_low: Optional[float] = None
    recent_swing_wick_high: Optional[float] = None

    # Active zone being tested
    zone_in_play: Optional[Zone] = None

    last_4h_bar: Optional[Bar] = None
    last_30m_bar: Optional[Bar] = None
    last_5m_bar: Optional[Bar] = None


class TrendContinuationConfig(StrategyConfig, kw_only=True):
    """Isolated configuration for TrendContinuationSMC strategy."""
    ema_fast_period: int = 50
    ema_slow_period: int = 200
    atr_period: int = 14
    displacement_multiplier: float = 1.5
    risk_per_trade_pct: float = 0.01  # 1% equity risk
    reward_to_risk_ratio: float = 2.5  # 2.5:1 TP
    sl_buffer_atr_factor: float = 0.5
    max_active_positions: int = 4
    max_unmitigated_zones: int = 4
    venue: str = "HYPERLIQUID"


class TrendContinuationSMC(Strategy):
    """
    NautilusTrader implementation of TrendContinuationSMC.
    """

    def __init__(self, config: TrendContinuationConfig, portfolio_guard: Optional[Any] = None) -> None:
        super().__init__(config)
        self.trend_config: TrendContinuationConfig = config
        self.venue = Venue(config.venue)
        self.portfolio_guard = portfolio_guard
        self.states: Dict[str, SMCInstrumentState] = {}
        self.instruments_map: Dict[str, Instrument] = {}

    def on_start(self) -> None:
        """Subscribe to 4H, 30M, and 5M bars for designated instruments."""
        instruments = [i for i in self.cache.instruments() if str(i.id.venue) == self.trend_config.venue]
        self.log.info(f"TrendContinuationSMC discovered {len(instruments)} instruments")

        for instrument in instruments:
            instr_id = instrument.id
            instr_str = str(instr_id)
            self.instruments_map[instr_str] = instrument

            state = SMCInstrumentState(
                instrument_id=instr_id,
                ema_50_4h=ExponentialMovingAverage(self.trend_config.ema_fast_period),
                ema_200_4h=ExponentialMovingAverage(self.trend_config.ema_slow_period),
                atr_30m=AverageTrueRange(self.trend_config.atr_period),
                atr_5m=AverageTrueRange(self.trend_config.atr_period),
            )
            self.states[instr_str] = state

            # Subscribe to multi-timeframe bars
            self.subscribe_bars(BarType.from_str(f"{instr_id}-4-HOUR-LAST-EXTERNAL"))
            self.subscribe_bars(BarType.from_str(f"{instr_id}-30-MINUTE-LAST-EXTERNAL"))
            self.subscribe_bars(BarType.from_str(f"{instr_id}-5-MINUTE-LAST-EXTERNAL"))

    def on_bar(self, bar: Bar) -> None:
        """Route incoming bars to respective timeframe logic."""
        instr_str = str(bar.bar_type.instrument_id)
        state = self.states.get(instr_str)
        if state is None:
            return

        bar_spec = str(bar.bar_type.spec)

        if "4-HOUR" in bar_spec:
            self._handle_4h_bar(bar, state)
        elif "30-MINUTE" in bar_spec:
            self._handle_30m_bar(bar, state)
        elif "5-MINUTE" in bar_spec:
            self._handle_5m_bar(bar, state)

    def _handle_4h_bar(self, bar: Bar, state: SMCInstrumentState) -> None:
        """
        4H Regime Logic:
        Bullish: 50 EMA slope > 0 and price > 200 EMA
        Bearish: 50 EMA slope < 0 and price < 200 EMA
        """
        state.last_4h_bar = bar
        close_px = bar.close.as_double()

        state.ema_50_4h.handle_bar(bar)
        state.ema_200_4h.handle_bar(bar)

        if state.ema_50_4h.initialized:
            current_ema50 = state.ema_50_4h.value
            if state.prev_ema_50 is not None:
                state.ema_50_slope = current_ema50 - state.prev_ema_50
            state.prev_ema_50 = current_ema50

        if state.ema_50_4h.initialized and state.ema_200_4h.initialized:
            ema200 = state.ema_200_4h.value
            if state.ema_50_slope > 0 and close_px > ema200:
                state.trend_state = "BULLISH"
            elif state.ema_50_slope < 0 and close_px < ema200:
                state.trend_state = "BEARISH"
            else:
                state.trend_state = "NEUTRAL"
        else:
            state.trend_state = "NEUTRAL"

    def _handle_30m_bar(self, bar: Bar, state: SMCInstrumentState) -> None:
        """
        30M Level Logic:
        Identify unmitigated Demand/Supply zones formed by high-volume displacement
        using smartmoneyconcepts.
        """
        state.last_30m_bar = bar
        state.atr_30m.handle_bar(bar)
        
        state.recent_30m_bars.append(bar)
        if len(state.recent_30m_bars) > 50:
            state.recent_30m_bars.pop(0)
            
        if len(state.recent_30m_bars) < 5:
            return

        # EXCEPTION TO ZERO-PANDAS GUIDELINE:
        # The third-party `smartmoneyconcepts` library (smc.fvg) strictly requires a pandas DataFrame
        # input with columns ['open', 'high', 'low', 'close', 'volume'].
        # To optimize performance, we construct column vectors directly in a Polars DataFrame
        # and convert via `.to_pandas()` exclusively at the boundary call to this external library.
        df_pl = pl.DataFrame({
            'open': [b.open.as_double() for b in state.recent_30m_bars],
            'high': [b.high.as_double() for b in state.recent_30m_bars],
            'low': [b.low.as_double() for b in state.recent_30m_bars],
            'close': [b.close.as_double() for b in state.recent_30m_bars],
            'volume': [b.volume.as_double() for b in state.recent_30m_bars],
        })
        fvg_df = smc.fvg(df_pl.to_pandas())
        
        close_px = bar.close.as_double()
        # Find latest FVG that is not mitigated
        latest_idx = fvg_df['FVG'].last_valid_index()
        if latest_idx is not None and fvg_df.loc[latest_idx, 'FVG'] != 0:
            fvg_val = fvg_df.loc[latest_idx, 'FVG']
            top = fvg_df.loc[latest_idx, 'Top']
            bottom = fvg_df.loc[latest_idx, 'Bottom']
            
            # Demand FVG
            if fvg_val == 1 and not any(z.ts_event == bar.ts_event for z in state.demand_zones):
                state.demand_zones.append(Zone(
                    zone_type="DEMAND", low=bottom, high=top, ts_event=bar.ts_event
                ))
                if len(state.demand_zones) > self.trend_config.max_unmitigated_zones:
                    state.demand_zones.pop(0)
            # Supply FVG
            elif fvg_val == -1 and not any(z.ts_event == bar.ts_event for z in state.supply_zones):
                state.supply_zones.append(Zone(
                    zone_type="SUPPLY", low=bottom, high=top, ts_event=bar.ts_event
                ))
                if len(state.supply_zones) > self.trend_config.max_unmitigated_zones:
                    state.supply_zones.pop(0)

        # Invalidate broken zones
        state.demand_zones = [z for z in state.demand_zones if not (z.mitigated or close_px < z.low)]
        state.supply_zones = [z for z in state.supply_zones if not (z.mitigated or close_px > z.high)]

    def _handle_5m_bar(self, bar: Bar, state: SMCInstrumentState) -> None:
        """
        5M Trigger Logic:
        Market Structure Shift (MSS) candle close breaking the previous 5M swing fractal upon zone touch.
        """
        state.last_5m_bar = bar
        state.atr_5m.handle_bar(bar)

        high_px = bar.high.as_double()
        low_px = bar.low.as_double()
        close_px = bar.close.as_double()

        state.recent_5m_bars.append(bar)
        if len(state.recent_5m_bars) > 30:
            state.recent_5m_bars.pop(0)

        self._update_5m_swing_points(state)

        if not self.portfolio.is_flat(state.instrument_id):
            return

        active_positions = self.cache.positions_open_count()
        if active_positions >= self.trend_config.max_active_positions:
            return

        # Bullish Continuation Trigger
        if state.trend_state == "BULLISH":
            for zone in state.demand_zones:
                if not zone.mitigated and (low_px <= zone.high and high_px >= zone.low):
                    zone.touched = True
                    state.zone_in_play = zone

            if state.zone_in_play and state.zone_in_play.zone_type == "DEMAND":
                # MSS: 5M candle close breaks previous 5M swing fractal
                if state.recent_swing_high and close_px > state.recent_swing_high:
                    self._execute_long_entry(bar, state, state.zone_in_play)

        # Bearish Continuation Trigger
        elif state.trend_state == "BEARISH":
            for zone in state.supply_zones:
                if not zone.mitigated and (high_px >= zone.low and low_px <= zone.high):
                    zone.touched = True
                    state.zone_in_play = zone

            if state.zone_in_play and state.zone_in_play.zone_type == "SUPPLY":
                # MSS: 5M candle close breaks previous 5M swing low fractal
                if state.recent_swing_low and close_px < state.recent_swing_low:
                    self._execute_short_entry(bar, state, state.zone_in_play)

    def _update_5m_swing_points(self, state: SMCInstrumentState) -> None:
        """Detect swing fractal highs and lows from recent 5M bars using SMC."""
        bars = state.recent_5m_bars
        if len(bars) < 5:
            return

        # EXCEPTION TO ZERO-PANDAS GUIDELINE:
        # The third-party `smartmoneyconcepts` library (smc.swing_highs_lows) strictly requires a
        # pandas DataFrame with columns ['open', 'high', 'low', 'close', 'volume'].
        # Column vectors are constructed in Polars and converted to pandas once via `.to_pandas()`.
        df_pl = pl.DataFrame({
            'open': [b.open.as_double() for b in bars],
            'high': [b.high.as_double() for b in bars],
            'low': [b.low.as_double() for b in bars],
            'close': [b.close.as_double() for b in bars],
            'volume': [b.volume.as_double() for b in bars],
        })
        df = df_pl.to_pandas()
        swing_data = smc.swing_highs_lows(df, swing_length=2)
        
        highs = swing_data[swing_data['HighLow'] == 1]
        lows = swing_data[swing_data['HighLow'] == -1]
        
        last_high = highs.last_valid_index() if not highs.empty else None
        last_low = lows.last_valid_index() if not lows.empty else None
        
        if last_high is not None:
            state.recent_swing_high = highs.loc[last_high, 'Level']
            state.recent_swing_wick_high = df.loc[last_high, 'high']
        
        if last_low is not None:
            state.recent_swing_low = lows.loc[last_low, 'Level']
            state.recent_swing_wick_low = df.loc[last_low, 'low']

    def _execute_long_entry(self, bar: Bar, state: SMCInstrumentState, zone: Zone) -> None:
        """1% equity risk bracket order with STOP_MARKET below the 5M swing wick."""
        instrument = self.instruments_map.get(str(state.instrument_id))
        if not instrument:
            return

        entry_px = bar.close.as_double()
        atr = state.atr_5m.value if state.atr_5m.initialized else (entry_px * 0.005)
        sl_buffer = atr * self.trend_config.sl_buffer_atr_factor

        # Stop loss placed below the 5M swing wick
        swing_wick = state.recent_swing_wick_low if state.recent_swing_wick_low else zone.low
        sl_price = min(zone.low, swing_wick, bar.low.as_double()) - sl_buffer
        if sl_price >= entry_px:
            sl_price = entry_px * 0.99

        risk_per_unit = entry_px - sl_price
        if risk_per_unit <= 0:
            return

        equity = self._get_account_equity()
        risk_usd = equity * self.trend_config.risk_per_trade_pct
        qty_val = risk_usd / risk_per_unit
        
        # Enforce official Hyperliquid exchange max leverage for this coin
        coin = str(instrument.id).split("-")[0].split(".")[0].upper()
        max_lev = get_coin_max_leverage(coin)
        max_notional = equity * max_lev
        if (qty_val * entry_px) > max_notional:
            qty_val = max_notional / entry_px

        tp_price = entry_px + (self.trend_config.reward_to_risk_ratio * risk_per_unit)

        quantity = instrument.make_qty(Decimal(str(round(qty_val, instrument.size_precision))))
        if quantity.as_double() <= 0:
            return

        notional_usd = qty_val * entry_px
        if self.portfolio_guard is not None:
            self.portfolio_guard.update_equity(equity)
            can_trade, reason = self.portfolio_guard.can_open_position(
                strategy_name=self.__class__.__name__,
                instrument_id=state.instrument_id,
                side=OrderSide.BUY,
                proposed_notional_usd=notional_usd,
                current_open_positions_count=self.cache.positions_open_count(),
            )
            if not can_trade:
                if reason != "SILENT_BLOCK":
                    self.log.warning(f"PortfolioGuard blocked long entry on {state.instrument_id}: {reason}")
                return

        coin = str(state.instrument_id).split("-")[0].split(".")[0].upper()
        if self.portfolio_guard and getattr(self.portfolio_guard, "prospect_biases", None):
            p_bias = self.portfolio_guard.prospect_biases.get(coin)
            if p_bias == "LONG":
                self.log.info(f"🎯 AI Prospect confirmed LONG bias on {state.instrument_id}")

        tp_price_obj = instrument.make_price(Decimal(str(round(tp_price, instrument.price_precision))))
        sl_price_obj = instrument.make_price(Decimal(str(round(sl_price, instrument.price_precision))))

        self.log.info(
            f"🟢 TrendContinuationSMC LONG {state.instrument_id}: Qty={quantity} @ ~{entry_px:.4f} | "
            f"SL={sl_price:.4f} (isTrigger=True) | TP={tp_price:.4f}"
        )

        try:
            bracket_list = self.order_factory.bracket(
                instrument_id=state.instrument_id,
                order_side=OrderSide.BUY,
                quantity=quantity,
                entry_order_type=OrderType.MARKET,
                tp_order_type=OrderType.LIMIT,
                tp_price=tp_price_obj,
                sl_order_type=OrderType.STOP_MARKET,
                sl_trigger_price=sl_price_obj,
            )
            self.submit_order_list(bracket_list)
            if self.portfolio_guard is not None and bracket_list.first is not None:
                self.portfolio_guard.register_order_submitted(
                    order=bracket_list.first,
                    strategy_name=self.__class__.__name__,
                    notional_usd=notional_usd,
                )
            zone.mitigated = True
            state.zone_in_play = None
        except Exception as e:
            self.log.error(f"Failed long bracket order: {e}")

    def _execute_short_entry(self, bar: Bar, state: SMCInstrumentState, zone: Zone) -> None:
        """1% equity risk bracket order with STOP_MARKET above the 5M swing wick."""
        instrument = self.instruments_map.get(str(state.instrument_id))
        if not instrument:
            return

        entry_px = bar.close.as_double()
        atr = state.atr_5m.value if state.atr_5m.initialized else (entry_px * 0.005)
        sl_buffer = atr * self.trend_config.sl_buffer_atr_factor

        # Stop loss placed above the 5M swing wick
        swing_wick = state.recent_swing_wick_high if state.recent_swing_wick_high else zone.high
        sl_price = max(zone.high, swing_wick, bar.high.as_double()) + sl_buffer
        if sl_price <= entry_px:
            sl_price = entry_px * 1.01

        risk_per_unit = sl_price - entry_px
        if risk_per_unit <= 0:
            return

        equity = self._get_account_equity()
        risk_usd = equity * self.trend_config.risk_per_trade_pct
        qty_val = risk_usd / risk_per_unit
        
        # Enforce official Hyperliquid exchange max leverage for this coin
        coin = str(instrument.id).split("-")[0].split(".")[0].upper()
        max_lev = get_coin_max_leverage(coin)
        max_notional = equity * max_lev
        if (qty_val * entry_px) > max_notional:
            qty_val = max_notional / entry_px

        tp_price = entry_px - (self.trend_config.reward_to_risk_ratio * risk_per_unit)

        quantity = instrument.make_qty(Decimal(str(round(qty_val, instrument.size_precision))))
        if quantity.as_double() <= 0:
            return

        notional_usd = qty_val * entry_px
        if self.portfolio_guard is not None:
            self.portfolio_guard.update_equity(equity)
            can_trade, reason = self.portfolio_guard.can_open_position(
                strategy_name=self.__class__.__name__,
                instrument_id=state.instrument_id,
                side=OrderSide.SELL,
                proposed_notional_usd=notional_usd,
                current_open_positions_count=self.cache.positions_open_count(),
            )
            if not can_trade:
                if reason != "SILENT_BLOCK":
                    self.log.warning(f"PortfolioGuard blocked short entry on {state.instrument_id}: {reason}")
                return

        coin = str(state.instrument_id).split("-")[0].split(".")[0].upper()
        if self.portfolio_guard and getattr(self.portfolio_guard, "prospect_biases", None):
            p_bias = self.portfolio_guard.prospect_biases.get(coin)
            if p_bias == "SHORT":
                self.log.info(f"🎯 AI Prospect confirmed SHORT bias on {state.instrument_id}")

        tp_price_obj = instrument.make_price(Decimal(str(round(tp_price, instrument.price_precision))))
        sl_price_obj = instrument.make_price(Decimal(str(round(sl_price, instrument.price_precision))))

        self.log.info(
            f"🔴 TrendContinuationSMC SHORT {state.instrument_id}: Qty={quantity} @ ~{entry_px:.4f} | "
            f"SL={sl_price:.4f} (isTrigger=True) | TP={tp_price:.4f}"
        )

        try:
            bracket_list = self.order_factory.bracket(
                instrument_id=state.instrument_id,
                order_side=OrderSide.SELL,
                quantity=quantity,
                entry_order_type=OrderType.MARKET,
                tp_order_type=OrderType.LIMIT,
                tp_price=tp_price_obj,
                sl_order_type=OrderType.STOP_MARKET,
                sl_trigger_price=sl_price_obj,
            )
            self.submit_order_list(bracket_list)
            if self.portfolio_guard is not None and bracket_list.first is not None:
                self.portfolio_guard.register_order_submitted(
                    order=bracket_list.first,
                    strategy_name=self.__class__.__name__,
                    notional_usd=notional_usd,
                )
            zone.mitigated = True
            state.zone_in_play = None
        except Exception as e:
            self.log.error(f"Failed short bracket order: {e}")

    def on_position_closed(self, event: Any) -> None:
        """Release margin from PortfolioGuard on position close."""
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
        """Safely fetch total account equity in USD."""
        try:
            account = self.portfolio.account(self.venue)
            if account is not None:
                bal = account.balance_total(USD)
                if bal is not None:
                    return float(bal.as_double())
        except Exception:
            pass
        return 100.0


# Backward compatibility aliases
TrendContinuationStrategy = TrendContinuationSMC
InstrumentState = SMCInstrumentState

__all__ = [
    "TrendContinuationSMC",
    "TrendContinuationConfig",
    "TrendContinuationStrategy",
    "Zone",
    "SMCInstrumentState",
    "InstrumentState",
]
