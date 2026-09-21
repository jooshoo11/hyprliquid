with open("src/strategies/orderbook_scalp.py", "r") as f:
    code = f.read()

old_flat_check = """        if not instrument or not self.portfolio.is_flat(instrument.id):
            return"""
new_flat_check = """        if not instrument or not self.portfolio.is_flat(instrument.id):
            return
            
        # Prevent spamming multiple limit orders before the first one fills
        if len(self.cache.orders_open(instrument.id)) > 0:
            return"""

code = code.replace(old_flat_check, new_flat_check)

with open("src/strategies/orderbook_scalp.py", "w") as f:
    f.write(code)

print("patched orderbook_scalp to check open orders!")
