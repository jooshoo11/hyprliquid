with open("src/risk/portfolio_guard.py", "r") as f:
    code = f.read()

old_code = """            if (current_strategy_margin + proposed_notional_usd) > max_allowed_margin:
                return False, (
                    f"Strategy '{strategy_name}' exceeds its allocation limit "
                    f"(${current_strategy_margin + proposed_notional_usd:,.2f} > ${max_allowed_margin:,.2f})."
                )"""

new_code = """            if (current_strategy_margin + proposed_notional_usd) > max_allowed_margin:
                import time
                if not hasattr(self, "_log_cooldown"):
                    self._log_cooldown = {}
                now = time.time()
                key = f"{strategy_name}_{instrument_id}"
                if now - self._log_cooldown.get(key, 0) < 5.0:
                    return False, "SILENT_BLOCK"
                self._log_cooldown[key] = now
                
                return False, (
                    f"Strategy '{strategy_name}' exceeds its allocation limit "
                    f"(${current_strategy_margin + proposed_notional_usd:,.2f} > ${max_allowed_margin:,.2f})."
                )"""

code = code.replace(old_code, new_code)
with open("src/risk/portfolio_guard.py", "w") as f:
    f.write(code)

print("patched!")
