import os
import re

base_dir = "/home/jooshoo/Desktop/hyprliquid/src"

# Fix continuation.py
cont_path = os.path.join(base_dir, "strategies/continuation.py")
with open(cont_path, "r") as f:
    cont_code = f.read()

cont_code = cont_code.replace("import pandas as pd", "import polars as pl")
cont_code = cont_code.replace("df = pd.DataFrame", "df = pl.DataFrame([{\n            'open': b.open.as_double(),\n            'high': b.high.as_double(),\n            'low': b.low.as_double(),\n            'close': b.close.as_double(),\n            'volume': b.volume.as_double()\n        } for b in bars]).to_pandas()  # type: ignore\n        # ")

# Clean up the old dict comprehension loop left behind
cont_code = re.sub(r"df = pl\.DataFrame\(\[\{\n.*?\}\s*for b in bars\]\)\.to_pandas\(\)\s*# type: ignore\s*#.*?\}\s*for b in bars\]\)", 
                   "df = pl.DataFrame([{'open': b.open.as_double(), 'high': b.high.as_double(), 'low': b.low.as_double(), 'close': b.close.as_double(), 'volume': b.volume.as_double()} for b in bars]).to_pandas()", 
                   cont_code, flags=re.DOTALL)

with open(cont_path, "w") as f:
    f.write(cont_code)

# Fix vbt_screener.py
vbt_path = os.path.join(base_dir, "optimization/vbt_screener.py")
with open(vbt_path, "r") as f:
    vbt_code = f.read()

vbt_code = vbt_code.replace("import pandas as pd", "import polars as pl\nimport math\nfrom datetime import datetime, timezone")

old_data = """        try:
            bars = catalog.bars([bar_type])
            for b in bars:
                data.append({
                    "coin": inst.id.symbol.value.replace("-USD-PERP", ""),
                    "ts": pd.to_datetime(b.ts_event, unit='ns'),
                    "close": b.close.as_double()
                })
        except Exception:
            continue
            
    if not data:
        raise ValueError("No 4H bar data found in catalog.")
        
    df = pd.DataFrame(data)
    price_df = df.pivot(index='ts', columns='coin', values='close')
    price_df = price_df.ffill().dropna()
    return price_df"""

new_data = """        try:
            bars = catalog.bars([bar_type])
            for b in bars:
                data.append({
                    "coin": inst.id.symbol.value.replace("-USD-PERP", ""),
                    "ts": datetime.fromtimestamp(b.ts_event / 1e9, tz=timezone.utc),
                    "close": b.close.as_double()
                })
        except Exception:
            continue
            
    if not data:
        raise ValueError("No 4H bar data found in catalog.")
        
    df = pl.DataFrame(data).to_pandas()
    price_df = df.pivot(index='ts', columns='coin', values='close')
    price_df = price_df.ffill().dropna()
    return price_df"""

vbt_code = vbt_code.replace(old_data, new_data)

vbt_code = vbt_code.replace("pd.isna(", "math.isnan(")
vbt_code = vbt_code.replace("-> pd.DataFrame:", "")

with open(vbt_path, "w") as f:
    f.write(vbt_code)

print("Pandas imports removed!")
