import os
import sys
import json
import time
import requests
import polars as pl

# 1. Fetch perp meta and contexts
resp_perp = requests.post('https://api.hyperliquid.xyz/info', json={'type': 'metaAndAssetCtxs'}, timeout=10)
perp_data = resp_perp.json()
perp_universe = perp_data[0]['universe']
perp_ctxs = perp_data[1]

# 2. Fetch spot meta and contexts
resp_spot = requests.post('https://api.hyperliquid.xyz/info', json={'type': 'spotMetaAndAssetCtxs'}, timeout=10)
spot_data = resp_spot.json()
spot_universe = spot_data[0]['universe']
spot_tokens = spot_data[0]['tokens']
spot_ctxs = spot_data[1]

print(f"Total perps: {len(perp_universe)}, Total spot pairs: {len(spot_universe)}, Total spot tokens: {len(spot_tokens)}")

# Map spot tokens
token_idx_to_name = {t['index']: t['name'] for t in spot_tokens}
spot_vol_by_token = {}
for pair, ctx in zip(spot_universe, spot_ctxs):
    base_idx = pair['tokens'][0]
    base_name = token_idx_to_name.get(base_idx, '')
    vlm = float(ctx.get('dayNtlVlm', 0.0))
    spot_vol_by_token[base_name] = spot_vol_by_token.get(base_name, 0.0) + vlm

# Process perps
perp_records = []
for u, ctx in zip(perp_universe, perp_ctxs):
    name = u['name']
    px = float(ctx.get('oraclePx', 0.0))
    prev_px = float(ctx.get('prevDayPx', 0.0))
    funding_hourly = float(ctx.get('funding', 0.0))
    funding_apr = funding_hourly * 24 * 365 * 100.0 # in %
    vol_24h = float(ctx.get('dayNtlVlm', 0.0))
    oi_tokens = float(ctx.get('openInterest', 0.0))
    oi_usd = oi_tokens * px
    change_24h = ((px - prev_px) / prev_px * 100.0) if prev_px > 0 else 0.0
    spot_vol = spot_vol_by_token.get(name, 0.0)
    
    perp_records.append({
        'coin': name,
        'price': px,
        'prev_price': prev_px,
        'change_24h': change_24h,
        'funding_hourly': funding_hourly,
        'funding_apr': funding_apr,
        'vol_24h': vol_24h,
        'oi_tokens': oi_tokens,
        'oi_usd': oi_usd,
        'spot_vol': spot_vol,
        'spot_perp_ratio': (spot_vol / vol_24h) if vol_24h > 0 else 0.0,
        'max_leverage': u.get('maxLeverage', 10),
    })

df_top50 = pl.DataFrame(perp_records).sort('vol_24h', descending=True).head(50)

print("\n--- TOP 50 PERPETUALS BY VOLUME ---")
for i, r in enumerate(df_top50.iter_rows(named=True), 1):
    print(f"{i:2d}. {r['coin']:<8} Px: ${r['price']:<10.4f} 24h: {r['change_24h']:+6.2f}% | Vol: ${r['vol_24h']/1e6:7.1f}M | Spot: ${r['spot_vol']/1e6:5.1f}M (S/P: {r['spot_perp_ratio']*100:4.1f}%) | Funding APR: {r['funding_apr']:+7.2f}% | OI: ${r['oi_usd']/1e6:6.1f}M")

# Save top 50 json for further processing
with open('/home/jooshoo/Desktop/hyprliquid/scratch/top50_snapshot.json', 'w') as f:
    json.dump(df_top50.to_dicts(), f, indent=2)

print("\nSnapshot saved to scratch/top50_snapshot.json")
