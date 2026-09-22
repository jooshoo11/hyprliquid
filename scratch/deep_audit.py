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

# Top 50 by volume
perp_records = []
for u, ctx in zip(perp_universe, perp_ctxs):
    name = u['name']
    px = float(ctx.get('oraclePx', 0.0))
    prev_px = float(ctx.get('prevDayPx', 0.0))
    funding_hourly = float(ctx.get('funding', 0.0))
    funding_apr = funding_hourly * 24 * 365 * 100.0 # %
    vol_24h = float(ctx.get('dayNtlVlm', 0.0))
    oi_tokens = float(ctx.get('openInterest', 0.0))
    oi_usd = oi_tokens * px
    change_24h = ((px - prev_px) / prev_px * 100.0) if prev_px > 0 else 0.0
    
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
        'max_leverage': u.get('maxLeverage', 10),
    })

df_all = pl.DataFrame(perp_records)
df_top50 = df_all.sort('vol_24h', descending=True).head(50)

# Check negative funding markets
negative_funding = df_all.filter(pl.col('funding_apr') < 0).sort('funding_apr')
print("=== NEGATIVE FUNDING MARKETS (SHORT SQUEEZE POTENTIAL) ===")
for r in negative_funding.iter_rows(named=True):
    print(f"{r['coin']:<10} Px: ${r['price']:<10.4f} 24h: {r['change_24h']:+6.2f}% | Vol: ${r['vol_24h']/1e6:6.2f}M | Funding APR: {r['funding_apr']:+7.2f}% | OI: ${r['oi_usd']/1e6:6.2f}M")

# Check crowded longs (>80% APR)
crowded_longs = df_top50.filter(pl.col('funding_apr') > 80.0).sort('funding_apr', descending=True)
print("\n=== CROWDED LONGS IN TOP 50 (>+80% APR) ===")
for r in crowded_longs.iter_rows(named=True):
    print(f"{r['coin']:<10} Px: ${r['price']:<10.4f} 24h: {r['change_24h']:+6.2f}% | Vol: ${r['vol_24h']/1e6:6.2f}M | Funding APR: {r['funding_apr']:+7.2f}% | OI: ${r['oi_usd']/1e6:6.2f}M")

# Orderbook depth analysis for key coins
coins_to_audit = ['ARB', 'DOGE', 'BTC', 'ETH', 'SOL', 'SUI', 'TAO', 'XMR', 'NEAR', 'CRV', 'kPEPE', 'ENA', 'USELESS', 'GRAM', 'XRP']
l2_results = {}

print("\n=== L2 ORDERBOOK MICROSTRUCTURE AUDIT ===")
for coin in coins_to_audit:
    try:
        resp = requests.post('https://api.hyperliquid.xyz/info', json={'type': 'l2Book', 'coin': coin}, timeout=10)
        book = resp.json()
        levels = book.get('levels', [[], []])
        bids = levels[0][:5]
        asks = levels[1][:5]
        
        bid_depth_usd = sum(float(b['px']) * float(b['sz']) for b in bids)
        ask_depth_usd = sum(float(a['px']) * float(a['sz']) for a in asks)
        
        # Top 10 levels
        bids_10 = levels[0][:10]
        asks_10 = levels[1][:10]
        bid_depth_10 = sum(float(b['px']) * float(b['sz']) for b in bids_10)
        ask_depth_10 = sum(float(a['px']) * float(a['sz']) for a in asks_10)
        
        total_5 = bid_depth_usd + ask_depth_usd
        skew_5 = (bid_depth_usd - ask_depth_usd) / total_5 if total_5 > 0 else 0.0
        ratio_5 = (ask_depth_usd / bid_depth_usd) if bid_depth_usd > 0 else 999.0
        
        best_bid = float(bids[0]['px']) if bids else 0.0
        best_ask = float(asks[0]['px']) if asks else 0.0
        spread_bps = ((best_ask - best_bid) / best_bid * 10000.0) if best_bid > 0 else 0.0
        
        l2_results[coin] = {
            'best_bid': best_bid,
            'best_ask': best_ask,
            'spread_bps': spread_bps,
            'bid_depth_5_usd': bid_depth_usd,
            'ask_depth_5_usd': ask_depth_usd,
            'bid_depth_10_usd': bid_depth_10,
            'ask_depth_10_usd': ask_depth_10,
            'depth_skew_5': skew_5,
            'ask_to_bid_ratio_5': ratio_5,
            'bid_to_ask_ratio_5': (bid_depth_usd / ask_depth_usd) if ask_depth_usd > 0 else 999.0,
        }
        
        print(f"{coin:<8} Spread: {spread_bps:4.1f} bps | Top5 Bids: ${bid_depth_usd/1e3:6.1f}k | Top5 Asks: ${ask_depth_usd/1e3:6.1f}k | Skew: {skew_5:+5.2f} | Ask/Bid: {ratio_5:4.2f}x")
    except Exception as e:
        print(f"Error fetching {coin}: {e}")

# Save detailed results
with open('/home/jooshoo/Desktop/hyprliquid/scratch/l2_audit.json', 'w') as f:
    json.dump(l2_results, f, indent=2)

# Audit active trades
with open('/home/jooshoo/Desktop/hyprliquid/bridge/active_trades.json', 'r') as f:
    active_trades = json.load(f)

print("\n=== ACTIVE TRADES RISK AUDIT ===")
for pos in active_trades.get('positions', []):
    coin = pos['coin']
    side = pos['side']
    size = pos['size']
    entry_px = pos['entry_price']
    mark_px = pos['mark_price']
    pnl = pos['unrealized_pnl']
    roi = pos['roi_pct']
    
    l2 = l2_results.get(coin, {})
    coin_data = df_all.filter(pl.col('coin') == coin).to_dicts()
    funding_apr = coin_data[0]['funding_apr'] if coin_data else 0.0
    
    print(f"\nPosition: {coin} {side}")
    print(f"  Size: {size} | Entry: ${entry_px} | Mark: ${mark_px} | PnL: ${pnl} ({roi}%)")
    print(f"  Funding APR: {funding_apr:+.2f}%")
    if l2:
        print(f"  L2 Top5 Bid Depth: ${l2['bid_depth_5_usd']/1e3:.1f}k | Ask Depth: ${l2['ask_depth_5_usd']/1e3:.1f}k")
        print(f"  Ask/Bid Ratio: {l2['ask_to_bid_ratio_5']:.2f}x | Depth Skew: {l2['depth_skew_5']:+.2f}")
        
        # Check toxic conditions:
        # 1. Ask depth > 4x Bid depth on LONG
        if side == 'LONG' and l2['ask_to_bid_ratio_5'] > 4.0:
            print(f"  [!] TOXIC CONDITION DETECTED: Ask depth wall > 4x Bid depth ({l2['ask_to_bid_ratio_5']:.2f}x)!")
        # 2. Funding APR > 120% on LONG
        if side == 'LONG' and funding_apr > 120.0:
            print(f"  [!] TOXIC CONDITION DETECTED: Funding rate {funding_apr:.1f}% > 120% APR threshold!")
        # 3. Heavy negative skew or adverse pressure
        if side == 'LONG' and l2['depth_skew_5'] < -0.5:
            print(f"  [WARNING] Severe orderbook bid collapse (skew {l2['depth_skew_5']:+.2f})")
    else:
        print("  L2 data not available")
