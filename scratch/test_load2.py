from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.data import BarType
import pandas as pd
import polars as pl

catalog = ParquetDataCatalog("catalog")
instruments = catalog.instruments()
data = []
for inst in instruments:
    bar_type = BarType.from_str(f"{inst.id}-4-HOUR-LAST-EXTERNAL")
    bars = catalog.bars([bar_type])
    if not bars:
        continue
    for b in bars:
        data.append({
            "coin": inst.id.symbol.value,
            "ts": pd.to_datetime(b.ts_event, unit='ns'),
            "close": b.close.as_double()
        })

df = pd.DataFrame(data)
print(df.head())
