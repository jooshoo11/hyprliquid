import re

with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

# Replace the slow funding call
old_funding = """
        # Funding APR
        funding_rate = info_client.get_funding_rate(coin) if hasattr(info_client, "get_funding_rate") else None
        if funding_rate is not None:
            funding_col = "bold red" if funding_rate > 0.50 else ("bold green" if funding_rate < -0.50 else "white")
            funding_str = f"[{funding_col}]{funding_rate:+.1%}[/{funding_col}]"
        else:
            funding_str = "[dim]--[/dim]"

        # Position tracking across strategies
"""

new_funding = """
        # Funding APR (from batched ctx_map)
        funding_rate = None
        if "funding" in ctx:
            funding_rate = float(ctx["funding"]) * 24 * 365
            
        if funding_rate is not None:
            funding_col = "bold red" if funding_rate > 0.50 else ("bold green" if funding_rate < -0.50 else "white")
            funding_str = f"[{funding_col}]{funding_rate:+.1%}[/{funding_col}]"
        else:
            funding_str = "[dim]--[/dim]"

        # Position tracking across strategies
"""

code = code.replace(old_funding.strip(), new_funding.strip())

# Fix indentation issue on table.add_column("Symbol", justify="left", style="bold white")
code = code.replace('table.add_column("Symbol"', '    table.add_column("Symbol"')

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("funding patched!")
