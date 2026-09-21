from decimal import Decimal
import math
from typing import Dict, Any

from nautilus_trader.model.data import Bar, QuoteTick, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId, ClientOrderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.events import OrderFilled

from config.base_config import BaseBotConfig
from risk.position_guard import PositionGuard

class VwapState:
    def __init__(self):
        self.reset()
        
    def reset(self):
        self.current_day = None
        self.cum_vol_price = 0.0
        self.cum_vol = 0.0
        self.prices = []
        self.volumes = []
        self.vwap = 0.0
        self.std_dev = 0.0
        
    def update(self, bar: Bar) -> tuple[float, float]:
        bar_time = bar.ts_init.as_datetime()
        day = bar_time.date()
        if self.current_day is None or day != self.current_day:
            self.reset()
            self.current_day = day
            
        tp = (bar.high.as_double() + bar.low.as_double() + bar.close.as_double()) / 3.0
        vol = bar.volume.as_double()
        
        self.cum_vol_price += tp * vol
        self.cum_vol += vol
        
        self.prices.append(tp)
        self.volumes.append(vol)
        
        if self.cum_vol > 0:
            self.vwap = self.cum_vol_price / self.cum_vol
            
            var_sum = 0.0
            for p, v in zip(self.prices, self.volumes):
                var_sum += v * ((p - self.vwap) ** 2)
            variance = var_sum / self.cum_vol
            self.std_dev = math.sqrt(variance)
        else:
            self.vwap = tp
            self.std_dev = 0.0
            
        return self.vwap, self.std_dev


class VwapReversionStrategy(Strategy):
    def __init__(self, config: BaseBotConfig):
        super().__init__(config)
        self.bot_config = config
        self.risk_guard = PositionGuard(config)
        
        # State tracking
        self.market_data: Dict[str, Dict[str, Any]] = {
            instr: {
                "last_bar": None, 
                "last_quote": None,
                "vwap_state": VwapState(),
                "entry_order_ids": set(),
            } 
            for instr in config.instrument_ids
        }
        
    def on_start(self):
        """Called when strategy starts."""
        self.log.info(f"Available instruments: {[str(i.id) for i in self.cache.instruments()][:20]}")
        for instr_id_str in self.bot_config.instrument_ids:
            instrument_id = InstrumentId.from_str(instr_id_str)
            
            # Subscribe to Bar and QuoteTick
            bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
            self.subscribe_bars(bar_type)
            self.subscribe_quote_ticks(instrument_id)
            
            self.log.info(f"Subscribed to market data for {instrument_id}")

    def on_bar(self, bar: Bar):
        """Handle new bar data."""
        instrument_id_str = str(bar.instrument_id)
        if instrument_id_str in self.market_data:
            state = self.market_data[instrument_id_str]
            state["last_bar"] = bar
            state["vwap_state"].update(bar)
            self.evaluate_signal(bar.instrument_id)

    def on_quote_tick(self, tick: QuoteTick):
        """Handle new quote tick data."""
        instrument_id_str = str(tick.instrument_id)
        if instrument_id_str in self.market_data:
            self.market_data[instrument_id_str]["last_quote"] = tick
            self.evaluate_signal(tick.instrument_id)

    def evaluate_signal(self, instrument_id: InstrumentId) -> int:
        """
        Evaluate market data to generate a trading signal.
        Returns: 1 for Long, -1 for Short, 0 for Neutral.
        """
        instr_str = str(instrument_id)
        state = self.market_data[instr_str]
        
        quote = state.get("last_quote")
        vwap_state = state.get("vwap_state")
        
        if not quote or vwap_state.cum_vol == 0:
            return 0
            
        # Already have an open position? Do not pyramid.
        position = self.portfolio.position(instrument_id)
        if position and position.quantity.as_double() != 0:
            return 0
            
        # Calculate Lower Band
        lower_band = vwap_state.vwap - (2.0 * vwap_state.std_dev)
        
        bid_price = quote.bid_price.as_double()
        bid_size = quote.bid_size.as_double()
        ask_size = quote.ask_size.as_double()
        
        total_size = bid_size + ask_size
        if total_size == 0:
            return 0
            
        bid_ratio = bid_size / total_size
        
        # Condition 1: Current price drops BELOW the Lower Band
        cond1 = bid_price < lower_band
        # Condition 2: L2 Order Book Bid Size / (Bid Size + Ask Size) > 0.70
        cond2 = bid_ratio > 0.70
        
        signal = 0
        if cond1 and cond2:
            signal = 1
            
        if signal != 0:
            self.execute_trade(instrument_id, signal)
            
        return signal

    def execute_trade(self, instrument_id: InstrumentId, signal: int):
        """Route orders with position guard checks."""
        instr_str = str(instrument_id)
        
        quote = self.market_data[instr_str].get("last_quote")
        if quote is None:
            return
            
        # Get portfolio equity
        equity_decimal = Decimal("100")
        if self.portfolio:
            base_balance = self.portfolio.balance(self.portfolio.base_currency)
            if base_balance:
                equity_decimal = base_balance.total.as_double()
                equity_decimal = Decimal(str(equity_decimal))
        
        self.risk_guard.update_equity(equity_decimal)
        if self.risk_guard.is_trading_halted:
            return
            
        instrument = self.cache.instrument(instrument_id)
        if not instrument:
            return

        price_decimal = quote.bid_price.as_decimal()
        qty_decimal = self.risk_guard.calculate_position_size(equity_decimal, price_decimal)
        
        if qty_decimal == 0:
            return
            
        qty = instrument.make_qty(qty_decimal)
        price = instrument.make_price(price_decimal)
        
        self.log.info(f"Executing LONG {qty} {instrument_id} @ {price}")
        
        order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY,
            quantity=qty,
            price=price,
            post_only=True,
            time_in_force=TimeInForce.GTC,
            tags=["ENTRY"]
        )
        
        self.market_data[instr_str]["entry_order_ids"].add(order.client_order_id)
        self.submit_order(order)

    def on_order_filled(self, event: OrderFilled):
        """Handle entry fill to place exit orders."""
        order = self.cache.order(event.client_order_id)
        if not order:
            return
            
        instr_str = str(event.instrument_id)
        state = self.market_data[instr_str]
        
        if "ENTRY" in order.tags:
            # Place TP and SL
            instrument = self.cache.instrument(event.instrument_id)
            qty = event.last_qty
            
            vwap_price = state["vwap_state"].vwap
            entry_price = float(event.last_price.as_double())
            
            # SL: 0.75% below entry
            sl_price_decimal = Decimal(str(entry_price * 0.9925))
            sl_price_decimal = self.risk_guard.clamp_stop_loss(sl_price_decimal, "SELL")
            
            tp_price_decimal = Decimal(str(vwap_price))
            
            if tp_price_decimal <= Decimal(str(entry_price)):
                tp_price_decimal = Decimal(str(entry_price * 1.001)) # Min profit fallback
                
            tp_price = instrument.make_price(tp_price_decimal)
            sl_price = instrument.make_price(sl_price_decimal)
            
            # TP Limit
            tp_order = self.order_factory.limit(
                instrument_id=event.instrument_id,
                order_side=OrderSide.SELL,
                quantity=qty,
                price=tp_price,
                reduce_only=True,
                tags=["TP"]
            )
            
            # SL Stop Market
            sl_order = self.order_factory.stop_market(
                instrument_id=event.instrument_id,
                order_side=OrderSide.SELL,
                quantity=qty,
                trigger_price=sl_price,
                reduce_only=True,
                tags=["SL"]
            )
            
            self.submit_order(tp_order)
            self.submit_order(sl_order)
            self.log.info(f"Placed Exits for {instr_str} - TP: {tp_price}, SL: {sl_price}")
