import os

cont_path = "/home/jooshoo/Desktop/hyprliquid/src/strategies/continuation.py"
with open(cont_path, "r") as f:
    cont_code = f.read()

cont_code = cont_code.replace("} for b in bars])\n        \n        fvg_df = smc.fvg(df)", "} for b in bars]).to_pandas()\n        \n        fvg_df = smc.fvg(df)")
cont_code = cont_code.replace("} for b in bars])\n        \n        swing_data = smc.swing_highs_lows(df, swing_length=2)", "} for b in bars]).to_pandas()\n        \n        swing_data = smc.swing_highs_lows(df, swing_length=2)")

with open(cont_path, "w") as f:
    f.write(cont_code)

pg_test_path = "/home/jooshoo/Desktop/hyprliquid/tests/test_portfolio_guard.py"
with open(pg_test_path, "r") as f:
    pg_test_code = f.read()

# Make sure we add update_equity(10_000.0) after guard = PortfolioGuard()
pg_test_code = pg_test_code.replace("guard = PortfolioGuard()", "guard = PortfolioGuard()\n    guard.update_equity(10_000.0)")

with open(pg_test_path, "w") as f:
    f.write(pg_test_code)

print("Fixed.")
