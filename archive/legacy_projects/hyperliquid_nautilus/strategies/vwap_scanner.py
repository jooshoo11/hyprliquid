from decimal import Decimal
import math
import pandas as pd
from typing import Dict, Any, List

from nautilus_trader.model.data import Bar, QuoteTick, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.trading.strategy import Strategy

from config.scanner_config import UniverseScannerConfig
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
        bar_time = pd.to_datetime(bar.ts_init, unit='ns')
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


class VwapScannerStrategy(Strategy):
    def __init__(self, config: UniverseScannerConfig):
        super().__init__(config)
        self.bot_config = config
        self.risk_guard = PositionGuard(config)
        
        self.market_data: Dict[str, Dict[str, Any]] = {}
        self.last_evaluated_minute = None

    def on_start(self):
        """Called when strategy starts."""
        instruments = [i for i in self.cache.instruments() if str(i.id).endswith("-USD-PERP.HYPERLIQUID")]
        self.log.info(f"Dynamically discovered {len(instruments)} perpetual instruments for VWAP Scanner")
        
        # Subscribe to all discovered instruments
        count = 0
        for instrument in instruments:
            instr_id = instrument.id
            bar_type = BarType.from_str(f"{instr_id}-1-MINUTE-LAST-EXTERNAL")
            
            self.subscribe_bars(bar_type)
            self.subscribe_quote_ticks(instr_id)
            
            self.market_data[str(instr_id)] = {
                "instrument_id": instr_id,
                "last_bar": None,
                "last_quote": None,
                "vwap_state": VwapState(),
            }
            count += 1
            
        self.log.info(f"Successfully subscribed to {count} instruments.")

    def on_bar(self, bar: Bar):
        """Handle new bar data."""
        instrument_id_str = str(bar.bar_type.instrument_id)
        if instrument_id_str in self.market_data:
            state = self.market_data[instrument_id_str]
            state["last_bar"] = bar
            state["vwap_state"].update(bar)
            
        current_minute = pd.to_datetime(bar.ts_init, unit='ns').minute
        if self.last_evaluated_minute != current_minute:
            self.last_evaluated_minute = current_minute
            self.evaluate_universe()

    def on_quote_tick(self, tick: QuoteTick):
        """Handle new quote tick data."""
        instrument_id_str = str(tick.instrument_id)
        if instrument_id_str in self.market_data:
            self.market_data[instrument_id_str]["last_quote"] = tick

    def evaluate_universe(self):
        """Rank and evaluate all subscribed pairs for VWAP overextension."""
        self.log.info("Running 60-second universe ranking evaluation...")
        
        candidates = []
        
        for instr_str, state in self.market_data.items():
            quote = state.get("last_quote")
            vwap_state = state.get("vwap_state")
            instr_id = state.get("instrument_id")
            
            if not quote or vwap_state.cum_vol == 0:
                continue
                
            # Skip if we already have a position here
            position = self.portfolio.position(instr_id)
            if position and position.quantity.as_double() != 0:
                continue
                
            lower_band = vwap_state.vwap - (2.0 * vwap_state.std_dev)
            
            bid_price = quote.bid_price.as_double()
            bid_size = quote.bid_size.as_double()
            ask_size = quote.ask_size.as_double()
            
            total_size = bid_size + ask_size
            if total_size == 0:
                continue
                
            bid_ratio = bid_size / total_size
            
            if bid_price < lower_band and bid_ratio > self.bot_config.imbalance_threshold:
                deviation = lower_band - bid_price
                candidates.append((deviation, instr_id, bid_price))
                
        if not candidates:
            return
            
        # Sort by greatest deviation below lower band
        candidates.sort(key=lambda x: x[0], reverse=True)
        
        # Check active positions for risk guard limits
        active_positions_count = len([p for p in self.portfolio.positions() if p.quantity.as_double() != 0])
        
        # Attempt execution on top candidates
        for deviation, instr_id, bid_price in candidates:
            if active_positions_count >= self.bot_config.max_concurrent_positions:
                break
                
            executed = self.execute_trade(instr_id, bid_price, active_positions_count)
            if executed:
                active_positions_count += 1

    def execute_trade(self, instrument_id: InstrumentId, bid_price: float, active_positions_count: int) -> bool:
        """Route orders with position guard checks."""
        # Get portfolio equity
        equity_decimal = Decimal("100")
        if self.portfolio:
            base_balance = self.portfolio.balance(self.portfolio.base_currency)
            if base_balance:
                equity_decimal = base_balance.total.as_double()
                equity_decimal = Decimal(str(equity_decimal))
        
        self.risk_guard.update_equity(equity_decimal)
        if self.risk_guard.is_trading_halted:
            return False
            
        instrument = self.cache.instrument(instrument_id)
        if not instrument:
            return False

        price_decimal = Decimal(str(bid_price))
        qty_decimal = self.risk_guard.calculate_position_size(equity_decimal, price_decimal, active_positions_count)
        
        if qty_decimal == 0:
            return False
            
        qty = instrument.make_qty(qty_decimal)
        price = instrument.make_price(price_decimal)
        
        self.log.info(f"Executing SCANNER LONG {qty} {instrument_id} @ {price}")
        
        order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY,
            quantity=qty,
            price=price,
            post_only=True,
            time_in_force=TimeInForce.GTC,
            tags=["ENTRY"]
        )
        
        self.submit_order(order)
        return True

    def on_order_filled(self, event: OrderFilled):
        """Handle entry fill to place exit orders."""
        order = self.cache.order(event.client_order_id)
        if not order:
            return
            
        instr_str = str(event.instrument_id)
        state = self.market_data[instr_str]
        
        if "ENTRY" in order.tags:
            instrument = self.cache.instrument(event.instrument_id)
            qty = event.last_qty
            
            vwap_price = state["vwap_state"].vwap
            entry_price = float(event.last_price.as_double())
            
            sl_pct = self.bot_config.default_sl_pct
            sl_price_decimal = Decimal(str(entry_price * (1.0 - sl_pct)))
            sl_price_decimal = self.risk_guard.clamp_stop_loss(sl_price_decimal, "SELL")
            
            tp_price_decimal = Decimal(str(vwap_price))
            
            if tp_price_decimal <= Decimal(str(entry_price)):
                tp_price_decimal = Decimal(str(entry_price * (1.0 + self.bot_config.default_tp_pct)))
                
            tp_price = instrument.make_price(tp_price_decimal)
            sl_price = instrument.make_price(sl_price_decimal)
            
            tp_order = self.order_factory.limit(
                instrument_id=event.instrument_id,
                order_side=OrderSide.SELL,
                quantity=qty,
                price=tp_price,
                reduce_only=True,
                tags=["TP"]
            )
            
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
