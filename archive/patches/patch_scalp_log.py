with open("src/strategies/orderbook_scalp.py", "r") as f:
    code = f.read()

old_log = """            if not can_trade:
                self.log.warning(f"PortfolioGuard blocked scalp entry on {instrument.id}: {reason}")
                return"""
                
new_log = """            if not can_trade:
                if reason != "SILENT_BLOCK":
                    self.log.warning(f"PortfolioGuard blocked scalp entry on {instrument.id}: {reason}")
                return"""

code = code.replace(old_log, new_log)

with open("src/strategies/orderbook_scalp.py", "w") as f:
    f.write(code)

print("patched scalp strategy logging!")
