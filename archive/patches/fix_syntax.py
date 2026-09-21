import re

path = "src/backtest/run_backtest.py"
with open(path, "r") as f:
    code = f.read()

code = code.replace("def print_comparison_table\n\ndef print_comparison_table(", "def print_comparison_table(")

with open(path, "w") as f:
    f.write(code)

