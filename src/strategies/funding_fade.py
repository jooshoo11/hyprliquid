"""
Strategy 2: HourlyFundingFade (funding_fade.py)
Captures pre-settlement unwinds from extreme hourly funding rates on Hyperliquid.
- Timing: Triggers between minute :48 and :52 of every hour across Top 20 assets.
- Threshold: Extreme annualized funding rate (|APR| > 80%).
- Entry: Post-only maker limit orders fading the crowded side (-0.01% maker fee).
    * If Funding > +80% APR (longs pay shorts): Enter SHORT to fade crowded longs.
    * If Funding < -80% APR (shorts pay longs): Enter LONG to fade crowded shorts.
- Exit: Between minute :02 and :05 post-settlement, or on a 1.2% trailing stop.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional
import datetime
import polars as pl
import os

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from src.utils.instruments import get_coin_max_leverage



@dataclass
class FundingFadePosition:
    """Tracks an active funding fade trade."""
    side: OrderSide
    entry_price: float
    high_water_mark: float
    entry_minute: int
    entry_hour: int
    trailing_stop_pct: float = 0.012  # 1.2% trailing stop


class HourlyFundingFadeConfig(StrategyConfig, kw_only=True):
    """Configuration for HourlyFundingFade strategy."""
    min_funding_apr_threshold: float = 0.40  # 40% APR for active carry opportunities
    entry_window_start_min: int = 40
    entry_window_end_min: int = 55
    exit_window_start_min: int = 1
    exit_window_end_min: int = 6
    trailing_stop_pct: float = 0.012  # 1.2%
    risk_per_trade_pct: float = 0.0075  # 0.75% equity risk
    max_active_positions: int = 5
    venue: str = "HYPERLIQUID"


class HourlyFundingFade(Strategy):
    """
    NautilusTrader implementation of HourlyFundingFade.
    """

    def __init__(self, config: HourlyFundingFadeConfig, portfolio_guard: Optional[Any] = None, info_client: Optional[Any] = None) -> None:
        super().__init__(config)
        self.fade_config: HourlyFundingFadeConfig = config
        self.venue = Venue(config.venue)
        self.portfolio_guard = portfolio_guard
        self.info_client = info_client
        self.instruments_map: Dict[str, Instrument] = {}
        self.active_fades: Dict[str, FundingFadePosition] = {}
        self.current_funding_rates: Dict[str, float] = {}
        self.prev_funding_rates: Dict[str, float] = {}

    def on_start(self) -> None:
        """Subscribe to 5M bars for designated instruments."""
        instruments = [i for i in self.cache.instruments() if str(i.id.venue) == self.fade_config.venue]
        self.log.info(f"HourlyFundingFade active across {len(instruments)} instruments")

        # Load historical funding data only if in backtest mode
        self.funding_df = None
        if getattr(self.cache, "is_backtest", False):
            funding_path = "catalog/funding/"
            if os.path.exists(funding_path):
                try:
                    self.funding_df = pl.scan_parquet(f"{funding_path}/**/*.parquet", hive_partitioning=True).collect()
                    self.log.info(f"Loaded historical funding from {funding_path}")
                except Exception as e:
                    self.log.warning(f"Could not load historical funding: {e}")

        for instrument in instruments:
            instr_str = str(instrument.id)
            self.instruments_map[instr_str] = instrument
            # Subscribe to 5M bars for minute-level resolution
            self.subscribe_bars(BarType.from_str(f"{instr_str}-5-MINUTE-LAST-EXTERNAL"))
            # Default estimated annualized funding rate
            self.current_funding_rates[instr_str] = 0.0

            if not getattr(self.cache, "is_backtest", False):
                self.subscribe_quote_ticks(instrument.id)

    def update_funding_rate(self, instrument_id_str: str, apr: float) -> None:
        """Allow live external feed or scanner to update hourly funding rate APR."""
        self.current_funding_rates[instrument_id_str] = apr

    def on_bar(self, bar: Bar) -> None:
        """Handle 5M bar checks for pre-settlement entry and post-settlement exit."""
        instr_str = str(bar.bar_type.instrument_id)
        instrument = self.instruments_map.get(instr_str)
        if not instrument:
            return

        dt = datetime.datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=datetime.timezone.utc)
        minute = dt.minute
        hour = dt.hour
        close_px = bar.close.as_double()

        # 1. Manage Active Positions (Trailing Stop & Post-Settlement Exit)
        if instr_str in self.active_fades:
            fade_pos = self.active_fades[instr_str]

            # Update High Water Mark
            if fade_pos.side == OrderSide.BUY:
                fade_pos.high_water_mark = max(fade_pos.high_water_mark, bar.high.as_double())
                drawdown = (fade_pos.high_water_mark - close_px) / fade_pos.high_water_mark
                # Trailing stop hit
                if drawdown >= fade_pos.trailing_stop_pct:
                    self._exit_fade(instrument, "Trailing Stop Triggered")
                    return
            else:
                fade_pos.high_water_mark = min(fade_pos.high_water_mark, bar.low.as_double())
                drawdown = (close_px - fade_pos.high_water_mark) / fade_pos.high_water_mark
                if drawdown >= fade_pos.trailing_stop_pct:
                    self._exit_fade(instrument, "Trailing Stop Triggered")
                    return

            # Exit between :02 and :05 of the new hour (post-settlement)
            if self.fade_config.exit_window_start_min <= minute <= self.fade_config.exit_window_end_min:
                if hour != fade_pos.entry_hour:
                    self._exit_fade(instrument, "Post-Settlement Time Window Exit")
                    return

            # Early exit on funding normalization: take profit early if extreme funding has collapsed
            cur_funding = self.current_funding_rates.get(instr_str, 0.0)
            if fade_pos.side == OrderSide.SELL and cur_funding > 0 and cur_funding <= 25.0:
                self._exit_fade(instrument, f"Funding Normalized: {cur_funding:+.1f}% APR (Early Profit Realized)")
                return
            elif fade_pos.side == OrderSide.BUY and cur_funding < 0 and cur_funding >= -10.0:
                self._exit_fade(instrument, f"Funding Normalized: {cur_funding:+.1f}% APR (Early Profit Realized)")
                return

        # 2. Check Pre-Settlement Entry between :48 and :52
        if self.fade_config.entry_window_start_min <= minute <= self.fade_config.entry_window_end_min:
            if not self.portfolio.is_flat(instrument.id):
                return

            # Prevent duplicate orders before previous order fills
            if len(self.cache.orders_open(instrument_id=instrument.id)) > 0:
                return

            if len(self.active_fades) >= self.fade_config.max_active_positions:
                return

            funding_apr = self.current_funding_rates.get(instr_str, 0.0)

            # If funding is not explicitly set, query live Info client if available
            if funding_apr == 0.0 and self.info_client is not None:
                coin = instr_str.split("-")[0].split(".")[0].upper()
                try:
                    live_rate = self.info_client.get_funding_rate(coin)
                    if live_rate is not None:
                        funding_apr = float(live_rate)
                        self.current_funding_rates[instr_str] = funding_apr
                except Exception:
                    pass

            # Fallback to historical funding dataframe if available
            if funding_apr == 0.0 and self.funding_df is not None:
                try:
                    cols = self.funding_df.columns
                    coin = instr_str.split("-")[0].split(".")[0].upper()
                    cond = pl.lit(True)
                    if "instrument_id" in cols:
                        cond = cond & (pl.col("instrument_id") == instr_str)
                    elif "coin" in cols:
                        cond = cond & (pl.col("coin") == coin)

                    if "timestamp" in cols:
                        cond = cond & (pl.col("timestamp") <= int(bar.ts_event / 1_000_000_000))
                    elif "time" in cols:
                        cond = cond & (pl.col("time") <= int(bar.ts_event / 1_000_000))

                    rate_col = "funding_rate" if "funding_rate" in cols else ("fundingRate" if "fundingRate" in cols else None)
                    if rate_col:
                        row = self.funding_df.filter(cond).tail(1)
                        if len(row) > 0:
                            funding_apr = row[rate_col].item() * 24 * 365
                except Exception:
                    pass

            if funding_apr == 0.0:
                # Estimate from bar momentum
                ret = (close_px - bar.open.as_double()) / bar.open.as_double()
                funding_apr = ret * 24 * 365 * 0.1

            # Funding velocity check: do not fight an accelerating funding spike
            prev_funding = self.prev_funding_rates.get(instr_str, funding_apr)
            self.prev_funding_rates[instr_str] = funding_apr
            funding_velocity = funding_apr - prev_funding

            # Extreme positive funding: FADE crowded longs -> SHORT (confirm funding velocity has stabilized <= +25% APR/5m)
            min_thresh = getattr(self, "dynamic_min_apr", self.fade_config.min_funding_apr_threshold)
            if funding_apr >= min_thresh and funding_velocity <= 25.0:
                self._enter_fade(instrument, OrderSide.SELL, close_px, minute, hour, funding_apr)

            # Extreme negative funding: FADE crowded shorts -> LONG (confirm downward funding spike has stabilized >= -25% APR/5m)
            elif funding_apr <= -min_thresh and funding_velocity >= -25.0:
                self._enter_fade(instrument, OrderSide.BUY, close_px, minute, hour, funding_apr)

    def _enter_fade(
        self,
        instrument: Instrument,
        side: OrderSide,
        price: float,
        minute: int,
        hour: int,
        funding_apr: float,
    ) -> None:
        """Submit post-only maker limit order to capture maker rebate (-0.01%)."""
        equity = self._get_account_equity()
        sizing_mult = 1.0
        if self.portfolio_guard and hasattr(self.portfolio_guard, "get_strategy_sizing_multiplier"):
            sizing_mult = self.portfolio_guard.get_strategy_sizing_multiplier(self.__class__.__name__)
        risk_usd = equity * self.fade_config.risk_per_trade_pct * sizing_mult
        risk_per_unit = price * self.fade_config.trailing_stop_pct
        if risk_per_unit <= 0:
            return

        qty_val = risk_usd / risk_per_unit
        
        # Enforce official Hyperliquid exchange max leverage for this coin, capped to 2.0x equity
        coin = str(instrument.id).split("-")[0].split(".")[0].upper()
        max_lev = get_coin_max_leverage(coin)
        max_fade_lev = min(float(max_lev), 2.0)
        max_notional = equity * max_fade_lev
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
                    self.log.warning(f"PortfolioGuard blocked fade entry on {instrument.id}: {reason}")
                return

        coin = str(instrument.id).split("-")[0].split(".")[0].upper()
        if self.portfolio_guard and getattr(self.portfolio_guard, "prospect_biases", None):
            p_bias = self.portfolio_guard.prospect_biases.get(coin)
            if (side == OrderSide.BUY and p_bias == "LONG") or (side == OrderSide.SELL and p_bias == "SHORT"):
                self.log.info(f"🎯 AI Prospect confirmed {p_bias} bias on {instrument.id}")

        limit_price = instrument.make_price(Decimal(str(round(price, instrument.price_precision))))

        self.log.info(
            f"⚡ HourlyFundingFade ENTRY {side} {instrument.id}: Qty={quantity} @ {limit_price} "
            f"(Funding APR: {funding_apr:+.1%}, Time: {hour:02d}:{minute:02d})"
        )

        try:
            order = self.order_factory.limit(
                instrument_id=instrument.id,
                order_side=side,
                quantity=quantity,
                price=limit_price,
                post_only=True,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
            if self.portfolio_guard is not None:
                self.portfolio_guard.register_order_submitted(
                    order=order,
                    strategy_name=self.__class__.__name__,
                    notional_usd=notional_usd,
                )
            self.active_fades[str(instrument.id)] = FundingFadePosition(
                side=side,
                entry_price=price,
                high_water_mark=price,
                entry_minute=minute,
                entry_hour=hour,
                trailing_stop_pct=self.fade_config.trailing_stop_pct,
            )
        except Exception as e:
            self.log.error(f"Failed to submit funding fade order: {e}")

    def _exit_fade(self, instrument: Instrument, reason: str) -> None:
        """Close fade position with market/taker sweep."""
        instr_str = str(instrument.id)
        fade_pos = self.active_fades.pop(instr_str, None)
        if not fade_pos:
            return

        self.log.info(f"🚪 HourlyFundingFade EXIT {instrument.id}: Reason='{reason}'")
        try:
            self.close_all_positions(instrument.id)
        except Exception as e:
            self.log.error(f"Failed to close fade position: {e}")

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
