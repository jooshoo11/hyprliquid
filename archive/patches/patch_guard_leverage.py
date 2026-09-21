with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

code = code.replace("max_strategy_equity_pct=10.0", "max_strategy_equity_pct=25.0")

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("patched PortfolioGuard!")
