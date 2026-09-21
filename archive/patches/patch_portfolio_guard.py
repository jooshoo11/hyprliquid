import time
with open("src/risk/portfolio_guard.py", "r") as f:
    code = f.read()

old_log = """        if strategy_total + proposed_notional_usd > strategy_limit:
            reason = f"Strategy '{strategy_name}' exceeds its allocation limit (${strategy_total + proposed_notional_usd:,.2f} > ${strategy_limit:,.2f})."
            return False, reason"""
            
new_log = """        if strategy_total + proposed_notional_usd > strategy_limit:
            reason = f"Strategy '{strategy_name}' exceeds its allocation limit (${strategy_total + proposed_notional_usd:,.2f} > ${strategy_limit:,.2f})."
            
            # Rate limit the reason string to avoid log spam
            import time
            if not hasattr(self, "_log_cooldown"):
                self._log_cooldown = {}
            now = time.time()
            key = f"{strategy_name}_{instrument.symbol}"
            if now - self._log_cooldown.get(key, 0) < 5.0:  # Only complain every 5 seconds per instrument
                reason = "SILENT_BLOCK" # Special flag
            else:
                self._log_cooldown[key] = now
            return False, reason"""

code = code.replace(old_log, new_log)

with open("src/risk/portfolio_guard.py", "w") as f:
    f.write(code)

print("patched portfolio guard!")
