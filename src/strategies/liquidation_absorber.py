"""
Strategy 6: LiquidationAbsorber (liquidation_absorber.py)
Microstructure Liquidation Cascade Absorption Strategy for Hyperliquid.

Core Alpha:
- Detects rapid forced liquidation cascades where aggressive market orders dislocate
  the mark price far beyond short-term fair value (> 2.0 - 2.5 std devs or > 2.0% dislocation from VWAP)
  accompanied by extreme volume spikes.
- Places passive post-only limit orders to absorb the forced liquidation exhaustion at favorable pricing,
  capturing the Hyperliquid -0.01% maker rebate.
- Targets mean reversion back to fair value (VWAP / mid-point) with strict stop-loss protection.
- Strictly zero pandas dependencies (pure Polars & vectorized NumPy).
- Strictly maintains NautilusTrader Cython object integrity (uses instrument.make_price / make_qty).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional
import time
import math
import numpy as np
import polars as pl

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from src.utils.instruments import get_coin_max_leverage


class LiquidationAbsorberConfig(StrategyConfig, kw_only=True):
    """Configuration for Liquidation Cascade Absorber."""
    venue: str = "HYPERLIQUID"
    bar_timeframe: str = "1-MINUTE"  # 1-MINUTE or 5-MINUTE
    dislocation_pct: float = 0.020  # Minimum 2.0% price dislocation from rolling VWAP
    volume_multiplier: float = 3.0  # Bar volume must exceed 3.0x 20-period rolling average
    stop_loss_pct: float = 0.015   # 1.5% stop behind absorption level
    take_profit_pct: float = 0.030 # 3.0% take profit target (mean reversion)
    max_active_positions: int = 1
    post_only: bool = True


@dataclass
class CascadeTracker:
    """State tracker for rolling volume, VWAP, and cascade dislocation detection."""
    instrument_id: InstrumentId
    recent_bars: List[Bar] = field(default_factory=list)
    rolling_volume: List[float] = field(default_factory=list)
    rolling_closes: List[float] = field(default_factory=list)
    cum_vol_price: float = 0.0
    cum_vol: float = 0.0
    vwap: float = 0.0
    last_signal_ts: int = 0


class LiquidationAbsorber(Strategy):
    """
    NautilusTrader implementation of Liquidation Cascade Absorption Strategy.
    """

    def __init__(self, config: LiquidationAbsorberConfig, portfolio_guard: Optional[Any] = None) -> None:
        super().__init__(config)
        self.absorber_config: LiquidationAbsorberConfig = config
        self.venue = Venue(config.venue)
        self.portfolio_guard = portfolio_guard
        self.instruments_map: Dict[str, Instrument] = {}
        self.state_map: Dict[str, CascadeTracker] = {}
        self._disabled: bool = False

    def on_start(self) -> None:
        """Initialize subscriptions and instruments."""
        if getattr(self.cache, "is_backtest", False):
            self.log.info("LiquidationAbsorber initializing in backtest mode.")

        for inst in self.cache.instruments():
            if str(inst.id.venue) == self.absorber_config.venue:
                instr_key = str(inst.id)
                self.instruments_map[instr_key] = inst
                self.state_map[instr_key] = CascadeTracker(instrument_id=inst.id)
                self.subscribe_bars(BarType.from_str(f"{instr_key}-{self.absorber_config.bar_timeframe}-LAST-EXTERNAL"))

        self.log.info(f"LiquidationAbsorber active on {len(self.instruments_map)} instruments.")

    def on_bar(self, bar: Bar) -> None:
        """Process incoming bar for liquidation cascade dislocations."""
        if self._disabled:
            return

        instr_key = str(bar.bar_type.instrument_id)
        instrument = self.instruments_map.get(instr_key)
        if not instrument:
            return

        state = self.state_map.setdefault(instr_key, CascadeTracker(instrument_id=bar.bar_type.instrument_id))
        close_px = bar.close.as_double()
        vol = bar.volume.as_double()

        state.recent_bars.append(bar)
        state.rolling_volume.append(vol)
        state.rolling_closes.append(close_px)

        if len(state.recent_bars) > 50:
            state.recent_bars.pop(0)
            state.rolling_volume.pop(0)
            state.rolling_closes.pop(0)

        # Update rolling session VWAP
        state.cum_vol_price += close_px * vol
        state.cum_vol += vol
        if state.cum_vol > 0:
            state.vwap = state.cum_vol_price / state.cum_vol

        if len(state.rolling_volume) < 20:
            return

        # 1. Volume spike check: current bar volume > multiplier * 20-bar avg volume
        avg_vol = float(np.mean(state.rolling_volume[:-1]))
        if avg_vol <= 0:
            return
        vol_ratio = vol / avg_vol
        if vol_ratio < self.absorber_config.volume_multiplier:
            return

        # 2. Price Dislocation check from VWAP
        if state.vwap <= 0:
            return

        dislocation = (close_px - state.vwap) / state.vwap

        # Downside cascade (Long absorption opportunity)
        if dislocation <= -self.absorber_config.dislocation_pct:
            # Long absorption: forced market liquidation exhaustion into bids
            entry_px = close_px
            sl_px = entry_px * (1.0 - self.absorber_config.stop_loss_pct)
            tp_px = max(state.vwap, entry_px * (1.0 + self.absorber_config.take_profit_pct))
            self._execute_absorption(instrument, OrderSide.BUY, entry_px, sl_px, tp_px, vol_ratio)

        # Upside squeeze cascade (Short absorption opportunity)
        elif dislocation >= self.absorber_config.dislocation_pct:
            # Short absorption: forced short covering exhaustion into asks
            entry_px = close_px
            sl_px = entry_px * (1.0 + self.absorber_config.stop_loss_pct)
            tp_px = min(state.vwap, entry_px * (1.0 - self.absorber_config.take_profit_pct))
            self._execute_absorption(instrument, OrderSide.SELL, entry_px, sl_px, tp_px, vol_ratio)

    def _execute_absorption(
        self,
        instrument: Instrument,
        side: OrderSide,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        vol_ratio: float,
    ) -> None:
        """Submit post-only absorption limit order using Nautilus instrument factory."""
        if hasattr(self, "cache") and self.cache:
            if len(self.cache.orders_open(instrument_id=instrument.id)) > 0:
                return
            open_pos = [p for p in self.cache.positions_open() if not p.is_closed and p.instrument_id == instrument.id]
            if open_pos:
                return

        equity = self._get_account_equity()
        if equity <= 0 or entry_price <= 0:
            return

        max_positions = float(getattr(self.portfolio_guard, "max_total_open_positions", 3) if self.portfolio_guard else 3)
        margin_allocated = equity / max_positions
        coin = str(instrument.id).split("-")[0].split(".")[0].upper()
        max_lev = min(float(get_coin_max_leverage(coin)), 5.0)
        target_notional = margin_allocated * max_lev

        qty_val = target_notional / entry_price
        quantity = instrument.make_qty(Decimal(str(round(qty_val, instrument.size_precision))))
        if quantity.as_double() <= 0:
            return

        notional_usd = qty_val * entry_price

        # Check PortfolioGuard
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
                return

        entry_obj = instrument.make_price(Decimal(str(round(entry_price, instrument.price_precision))))
        sl_obj = instrument.make_price(Decimal(str(round(sl_price, instrument.price_precision))))
        tp_obj = instrument.make_price(Decimal(str(round(tp_price, instrument.price_precision))))

        self.log.info(
            f"🌊 [LiquidationAbsorber] {side.name} {instrument.id}: Qty={quantity} @ {entry_obj} | "
            f"SL={sl_obj} | TP={tp_obj} | VolRatio={vol_ratio:.2f}x"
        )

        try:
            bracket_list = self.order_factory.bracket(
                instrument_id=instrument.id,
                order_side=side,
                quantity=quantity,
                entry_order_type=OrderType.LIMIT,
                entry_price=entry_obj,
                entry_post_only=self.absorber_config.post_only,
                time_in_force=TimeInForce.GTC,
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
                    timeout_seconds=30.0,
                )
        except Exception as e:
            self.log.warning(f"Error submitting absorption bracket order: {e}")

    def _get_account_equity(self) -> float:
        """Retrieve total equity from portfolio cache or guard."""
        if hasattr(self, "portfolio") and self.portfolio:
            try:
                base_currency = USD
                total = self.portfolio.equity(base_currency)
                if total is not None:
                    return float(total.as_double())
            except Exception:
                pass
        if self.portfolio_guard and hasattr(self.portfolio_guard, "current_equity"):
            return float(self.portfolio_guard.current_equity)
        return 1000.0
