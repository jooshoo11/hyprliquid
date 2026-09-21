import re

path = "src/strategies/orderbook_scalp.py"
with open(path, "r") as f:
    code = f.read()

code = code.replace("self.scalp_config.skew_threshold = 99999.0  # Disable triggers", "self._disabled = True")

# Inject `if getattr(self, "_disabled", False): return` at start of on_quote_tick and on_bar
code = code.replace("def on_quote_tick(self, tick: QuoteTick) -> None:\n        instr_str", "def on_quote_tick(self, tick: QuoteTick) -> None:\n        if getattr(self, '_disabled', False): return\n        instr_str")
code = code.replace("def on_bar(self, bar: Bar) -> None:\n        \"\"\"Fallback", "def on_bar(self, bar: Bar) -> None:\n        if getattr(self, '_disabled', False): return\n        \"\"\"Fallback")

with open(path, "w") as f:
    f.write(code)

print("patched")
