"""
Strategy 3: OrderBookImbalance (orderbook_scalp.py)
Microstructure order book imbalance scalper on Hyperliquid.
- Input: L2 Order Book Depth and Quote Ticks.
- Signal: Detects liquidity vacuums into large resting limit walls (bid/ask depth skew > 3.0).
- Execution: Posts passive post-only limit orders directly in front of the wall to earn -0.01% maker rebate.
- Risk: Tight 3-tick stop behind the wall with dual take-profit targets.
"""

import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from src.utils.instruments import get_coin_max_leverage



class OrderBookImbalanceConfig(StrategyConfig, kw_only=True):
    """Configuration for OrderBookImbalance scalper."""
    skew_threshold: float = 3.5  # Bid/Ask depth ratio > 3.5 (genuine wall, avoids noise)
    stop_ticks: int = 12  # 12-tick stop behind the wall
    take_profit_ticks: int = 24  # 24-tick target (2:1 reward-to-risk)
    risk_per_trade_pct: float = 0.0075  # 0.75% equity risk
    max_active_positions: int = 1  # Strictly 1 position max to prevent portfolio monopolization
    venue: str = "HYPERLIQUID"


class OrderBookImbalance(Strategy):
    """
    NautilusTrader implementation of OrderBookImbalance scalper.
    """

    def __init__(self, config: OrderBookImbalanceConfig, portfolio_guard: Optional[Any] = None) -> None:
        super().__init__(config)
        self.scalp_config: OrderBookImbalanceConfig = config
        self.venue = Venue(config.venue)
        self.portfolio_guard = portfolio_guard
        self.instruments_map: Dict[str, Instrument] = {}

    def on_start(self) -> None:
        """Subscribe to quote ticks and bars across venue instruments."""
        if getattr(self.cache, "is_backtest", False):
            self.log.info("LIVE_PAPER_ONLY: OrderBookImbalance logic disabled during historical backtest.")
            self._disabled = True
            
        instruments = [i for i in self.cache.instruments() if str(i.id.venue) == self.scalp_config.venue]
        self.log.info(f"OrderBookImbalance active on {len(instruments)} instruments")

        for instrument in instruments:
            instr_str = str(instrument.id)
            self.instruments_map[instr_str] = instrument
            self.subscribe_quote_ticks(instrument.id)
            self.subscribe_bars(BarType.from_str(f"{instr_str}-5-MINUTE-LAST-EXTERNAL"))

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """Handle raw quote ticks to evaluate order book imbalance."""
        instr_str = str(tick.instrument_id)
        instrument = self.instruments_map.get(instr_str)
        if not instrument or not self.portfolio.is_flat(instrument.id):
            return
            
        # Prevent spamming multiple limit orders before the first one fills
        if len(self.cache.orders_open(instrument_id=instrument.id)) > 0:
            return
            
        # Add a 1 second cooldown per instrument to prevent async spam
        if not hasattr(self, "_last_order_ts"):
            self._last_order_ts = {}
        if (tick.ts_event - self._last_order_ts.get(instr_str, 0)) < 1_000_000_000:
            return

        active_count = self.cache.positions_open_count()
        if active_count >= self.scalp_config.max_active_positions:
            return

        bid_size = tick.bid_size.as_double()
        ask_size = tick.ask_size.as_double()

        if bid_size <= 0 or ask_size <= 0:
            return

        tick_size = instrument.price_increment.as_double()

        # Check AI prospect bias: do not take scalp against prospect bias
        coin = str(instrument.id).split("-")[0].split(".")[0].upper()
        p_bias = getattr(self.portfolio_guard, "prospect_biases", {}).get(coin) if self.portfolio_guard else None

        skew_thresh = getattr(self, "dynamic_skew_threshold", self.scalp_config.skew_threshold)
        tp_ticks = getattr(self, "dynamic_take_profit_ticks", self.scalp_config.take_profit_ticks)

        # Bid Wall: Imbalance Skew >= threshold -> Buy in front of the wall
        bid_ask_skew = bid_size / ask_size
        if bid_ask_skew >= skew_thresh:
            if p_bias == "SHORT":
                return  # Skip BUY if AI Prospect bias is SHORT
            wall_px = tick.bid_price.as_double()
            entry_px = wall_px  # Post at best bid (maker)
            sl_px = wall_px - (self.scalp_config.stop_ticks * tick_size)
            tp_px = entry_px + (tp_ticks * tick_size)
            self._execute_scalp(instrument, OrderSide.BUY, entry_px, sl_px, tp_px, bid_ask_skew)
            return

        # Ask Wall: Imbalance Skew >= threshold -> Sell in front of the wall
        ask_bid_skew = ask_size / bid_size
        if ask_bid_skew >= skew_thresh:
            if p_bias == "LONG":
                return  # Skip SELL if AI Prospect bias is LONG
            wall_px = tick.ask_price.as_double()
            entry_px = wall_px  # Post at best ask (maker)
            sl_px = wall_px + (self.scalp_config.stop_ticks * tick_size)
            tp_px = entry_px - (tp_ticks * tick_size)
            self._execute_scalp(instrument, OrderSide.SELL, entry_px, sl_px, tp_px, ask_bid_skew)

    def on_bar(self, bar: Bar) -> None:
        if getattr(self, '_disabled', False): return
        """Fallback simulation for backtesting when quote ticks are synthesized from bars."""
        instr_str = str(bar.bar_type.instrument_id)
        instrument = self.instruments_map.get(instr_str)
        if not instrument or not self.portfolio.is_flat(instrument.id):
            return
            
        # Prevent spamming multiple limit orders before the first one fills
        if len(self.cache.orders_open(instrument_id=instrument.id)) > 0:
            return
            
        # Add a 1 second cooldown per instrument to prevent async spam
        if not hasattr(self, "_last_order_ts"):
            self._last_order_ts = {}
        if (bar.ts_event - self._last_order_ts.get(instr_str, 0)) < 1_000_000_000:
            return

        # Derive simulated imbalance from bar volume and range compression
        rng = bar.high.as_double() - bar.low.as_double()
        if rng <= 0:
            return

        # When close finishes in the top 10% of high volume bar -> buying wall
        upper_wick = bar.high.as_double() - bar.close.as_double()
        lower_wick = bar.close.as_double() - bar.low.as_double()
        tick_size = instrument.price_increment.as_double()

        if lower_wick > 0 and upper_wick / lower_wick > 4.0:
            # Rejection from high -> Ask wall
            entry_px = bar.close.as_double()
            sl_px = entry_px + (self.scalp_config.stop_ticks * tick_size)
            tp_px = entry_px - (self.scalp_config.take_profit_ticks * tick_size)
            self._execute_scalp(instrument, OrderSide.SELL, entry_px, sl_px, tp_px, 3.5)
        elif upper_wick > 0 and lower_wick / upper_wick > 4.0:
            # Rejection from low -> Bid wall
            entry_px = bar.close.as_double()
            sl_px = entry_px - (self.scalp_config.stop_ticks * tick_size)
            tp_px = entry_px + (self.scalp_config.take_profit_ticks * tick_size)
            self._execute_scalp(instrument, OrderSide.BUY, entry_px, sl_px, tp_px, 3.5)

    def _execute_scalp(
        self,
        instrument: Instrument,
        side: OrderSide,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        skew: float,
    ) -> None:
        """Submit passive bracket order in front of resting wall."""
        equity = self._get_account_equity()
        sizing_mult = 1.0
        if self.portfolio_guard and hasattr(self.portfolio_guard, "get_strategy_sizing_multiplier"):
            sizing_mult = self.portfolio_guard.get_strategy_sizing_multiplier(self.__class__.__name__)
        risk_usd = equity * self.scalp_config.risk_per_trade_pct * sizing_mult
        risk_per_unit = abs(entry_price - sl_price)
        if risk_per_unit <= 0:
            return

        qty_val = risk_usd / risk_per_unit
        
        # Enforce official Hyperliquid exchange max leverage for this coin
        coin = str(instrument.id).split("-")[0].split(".")[0].upper()
        max_lev = get_coin_max_leverage(coin)
        # Cap scalp notional to 2.0x equity to prevent astronomical sizing on tight tick stops
        max_scalp_lev = min(float(max_lev), 2.0)
        max_notional = equity * max_scalp_lev
        if (qty_val * entry_price) > max_notional:
            qty_val = max_notional / entry_price
        quantity = instrument.make_qty(Decimal(str(round(qty_val, instrument.size_precision))))
        if quantity.as_double() <= 0:
            return

        notional_usd = qty_val * entry_price
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
                    self.log.warning(f"PortfolioGuard blocked scalp entry on {instrument.id}: {reason}")
                return

        coin = str(instrument.id).split("-")[0].split(".")[0].upper()
        if self.portfolio_guard and getattr(self.portfolio_guard, "prospect_biases", None):
            p_bias = self.portfolio_guard.prospect_biases.get(coin)
            if (side == OrderSide.BUY and p_bias == "LONG") or (side == OrderSide.SELL and p_bias == "SHORT"):
                self.log.info(f"🎯 AI Prospect confirmed {p_bias} bias on {instrument.id}")

        entry_obj = instrument.make_price(Decimal(str(round(entry_price, instrument.price_precision))))
        sl_obj = instrument.make_price(Decimal(str(round(sl_price, instrument.price_precision))))
        tp_obj = instrument.make_price(Decimal(str(round(tp_price, instrument.price_precision))))

        instr_str = str(instrument.id)
        if not hasattr(self, "_last_order_ts"):
            self._last_order_ts = {}
        self._last_order_ts[instr_str] = int(time.time() * 1_000_000_000)

        msg = (
            f"🎯 OrderBookImbalance {side.name} {instrument.id}: Qty={quantity} @ {entry_obj} | "
            f"SL={sl_obj} | TP={tp_obj} | Skew={skew:.2f}"
        )
        self.log.info(msg)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

        try:
            bracket_list = self.order_factory.bracket(
                instrument_id=instrument.id,
                order_side=side,
                quantity=quantity,
                entry_order_type=OrderType.LIMIT,
                entry_price=entry_obj,
                entry_post_only=True,
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
            self.log.error(f"Failed to submit orderbook scalp bracket: {e}")

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
