import glob

for filename in glob.glob("src/strategies/*.py"):
    with open(filename, "r") as f:
        code = f.read()

    # Change fallback equity from 10,000 to 100
    code = code.replace("return 10_000.0", "return 100.0")

    # Add leverage cap
    if "qty_val = risk_usd / risk_per_unit" in code:
        old_qty = "qty_val = risk_usd / risk_per_unit"
        new_qty = """qty_val = risk_usd / risk_per_unit
        
        # Enforce max 20x leverage for $100 small account challenge
        max_notional = equity * 20.0
        if (qty_val * entry_price) > max_notional:
            qty_val = max_notional / entry_price"""
        code = code.replace(old_qty, new_qty)

    with open(filename, "w") as f:
        f.write(code)

print("patched leverage!")
