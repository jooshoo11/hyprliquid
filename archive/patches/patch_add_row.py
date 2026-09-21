import re

with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

old_add_row = """
        table.add_row(
            coin,
            price_str,
            trend_str,
            zones_str,
            struct_str,
            funding_str,
            pos_str,
        )
"""

new_add_row = """
        table.add_row(
            coin,
            price_str,
            gain_5m_str,
            gain_30m_str,
            g24_str,
            trend_str,
            zones_str,
            struct_str,
            funding_str,
            pos_str,
        )
"""

code = code.replace(old_add_row.strip(), new_add_row.strip())

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("add_row patched!")
