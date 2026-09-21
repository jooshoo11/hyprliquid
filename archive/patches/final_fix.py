import re

# 1. continuation.py
cont_path = "/home/jooshoo/Desktop/hyprliquid/src/strategies/continuation.py"
with open(cont_path, "r") as f:
    cont_code = f.read()

# Instead of replacing the df definition, just find "fvg_df = smc.fvg(df)" and replace it with:
# fvg_df = smc.fvg(df.to_pandas() if hasattr(df, "to_pandas") else df)
cont_code = cont_code.replace("fvg_df = smc.fvg(df)", "fvg_df = smc.fvg(df.to_pandas() if hasattr(df, 'to_pandas') else df)")
cont_code = cont_code.replace("swing_data = smc.swing_highs_lows(df, swing_length=2)", "swing_data = smc.swing_highs_lows(df.to_pandas() if hasattr(df, 'to_pandas') else df, swing_length=2)")

with open(cont_path, "w") as f:
    f.write(cont_code)

# 2. test_portfolio_guard.py
pg_test_path = "/home/jooshoo/Desktop/hyprliquid/tests/test_portfolio_guard.py"
with open(pg_test_path, "r") as f:
    pg_test_code = f.read()

pg_test_code = pg_test_code.replace("guard = PortfolioGuard(max_total_open_positions=4)", "guard = PortfolioGuard(max_total_open_positions=4)\n    guard.update_equity(10000.0)")

with open(pg_test_path, "w") as f:
    f.write(pg_test_code)

print("done")
