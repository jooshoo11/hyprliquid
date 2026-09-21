import os

base_dir = "/home/jooshoo/Desktop/hyprliquid/src"

# 1. continuation.py: add .to_pandas()
cont_path = os.path.join(base_dir, "strategies/continuation.py")
with open(cont_path, "r") as f:
    cont_code = f.read()

cont_code = cont_code.replace("} for b in bars])\n        \n        fvg_df = smc.fvg(df)", "} for b in bars]).to_pandas()\n        \n        fvg_df = smc.fvg(df)")
cont_code = cont_code.replace("} for b in bars])\n        \n        swing_data = smc.swing_highs_lows(df, swing_length=2)", "} for b in bars]).to_pandas()\n        \n        swing_data = smc.swing_highs_lows(df, swing_length=2)")

with open(cont_path, "w") as f:
    f.write(cont_code)

# 2. portfolio_guard.py
pg_path = os.path.join(base_dir, "risk/portfolio_guard.py")
with open(pg_path, "r") as f:
    pg_code = f.read()

# Make sure it sets HWM if None
old_equity = "        self.current_equity = equity\n        if equity > self.daily_high_water_mark:"
new_equity = "        self.current_equity = equity\n        if self.daily_high_water_mark is None:\n            self.daily_high_water_mark = equity\n        if equity > self.daily_high_water_mark:"
pg_code = pg_code.replace(old_equity, new_equity)

with open(pg_path, "w") as f:
    f.write(pg_code)

print("Remaining fixed!")
