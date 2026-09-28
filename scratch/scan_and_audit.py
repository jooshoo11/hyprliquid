import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import requests
import polars as pl
from datetime import datetime, timezone
from src.scanner.mcp_client import HyperliquidInfoClient

client = HyperliquidInfoClient(network="mainnet")

# 1. Fetch live engine state
try:
    resp = requests.get("http://localhost:8000/api/state", timeout=5)
    state = resp.json()
    print("=== LIVE STATE ===")
    print("Equity: $" + str(state.get("equity")))
    print("Cash: $" + str(state.get("cash_balance")))
    print("Positions count: " + str(len(state.get("positions", []))))
    for p in state.get("positions", []):
        c = p.get("coin")
        s = p.get("side")
        sz = p.get("size")
        epx = p.get("entry_price")
        mpx = p.get("mark_price")
        pnl = p.get("unrealized_pnl")
        roi = p.get("roi_pct")
        print(f"  {c} {s}: size={sz} entry={epx} mark={mpx} pnl=${pnl} roi={roi}%")
except Exception as e:
    print("API state error:", e)

# 2. Fetch top 50 perpetuals
meta, asset_ctxs = client.get_meta_and_asset_ctxs()
universe = meta.get("universe", [])
records = []
for u, ctx in zip(universe, asset_ctxs):
    name = u.get("name")
    px = float(ctx.get("oraclePx", 0.0))
    prev_px = float(ctx.get("prevDayPx", 0.0))
    funding = float(ctx.get("funding", 0.0)) * 24 * 365 * 100
    vol_24h = float(ctx.get("dayNtlVlm", 0.0))
    chg = ((px - prev_px) / prev_px * 100) if prev_px > 0 else 0.0
    records.append({"coin": name, "price": px, "funding_apr": funding, "vol_24h": vol_24h, "change_24h": chg})

df = pl.DataFrame(records).sort("vol_24h", descending=True).head(50)
print("\n=== TOP 20 MOVERS BY VOLUME ===")
for r in df.head(20).iter_rows(named=True):
    c = r["coin"]
    px = r["price"]
    vol = r["vol_24h"] / 1e6
    chg = r["change_24h"]
    fund = r["funding_apr"]
    print(f"{c:10} Px=${px:>10.4f} | Vol=${vol:>6.1f}M | Chg={chg:>+6.2f}% | Funding={fund:>+6.1f}% APR")

df_liquid = pl.DataFrame(records).filter(pl.col("vol_24h") > 1_000_000)
high_fund = df_liquid.sort("funding_apr", descending=True).head(5)
low_fund = df_liquid.sort("funding_apr", descending=False).head(5)

print("\n=== EXTREME HIGH FUNDING (OVERHEATED LONGS) ===")
for r in high_fund.iter_rows(named=True):
    c = r["coin"]
    print(f"{c:10} Funding={r['funding_apr']:>+6.1f}% APR | Vol=${r['vol_24h']/1e6:>6.1f}M | Chg={r['change_24h']:>+6.1f}%")

print("\n=== EXTREME LOW / NEGATIVE FUNDING (SHORT SQUEEZE POTENTIAL) ===")
for r in low_fund.iter_rows(named=True):
    c = r["coin"]
    print(f"{c:10} Funding={r['funding_apr']:>+6.1f}% APR | Vol=${r['vol_24h']/1e6:>6.1f}M | Chg={r['change_24h']:>+6.1f}%")
