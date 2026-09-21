with open("src/strategies/orderbook_scalp.py", "r") as f:
    code = f.read()

old_flat_check = """        # Prevent spamming multiple limit orders before the first one fills
        if len(self.cache.orders_open(instrument_id=instrument.id)) > 0:
            return"""
new_flat_check = """        # Prevent spamming multiple limit orders before the first one fills
        if len(self.cache.orders_open(instrument_id=instrument.id)) > 0:
            return
            
        # Add a 1 second cooldown per instrument to prevent async spam
        if not hasattr(self, "_last_order_ts"):
            self._last_order_ts = {}
        if (tick.ts_event - self._last_order_ts.get(instr_str, 0)) < 1_000_000_000:
            return"""

code = code.replace(old_flat_check, new_flat_check)

old_submit = """        self.submit_order_list(order_list)
        
        self.log.info(f"Submitted bracket scalp for {instrument.symbol} at {entry_price}")"""
new_submit = """        self.submit_order_list(order_list)
        self._last_order_ts[str(instrument.id)] = self.clock.utc_now().as_unix_nanos()
        self.log.info(f"Submitted bracket scalp for {instrument.symbol} at {entry_price}")"""

code = code.replace(old_submit, new_submit)

with open("src/strategies/orderbook_scalp.py", "w") as f:
    f.write(code)

print("patched cooldown!")
