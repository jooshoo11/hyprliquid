import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = str(Path(__file__).resolve().parents[1])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.scanner.mcp_client import HyperliquidInfoClient

def run_prospector_scan():
    client = HyperliquidInfoClient()
    meta_and_ctxs = client._info.meta_and_asset_ctxs()
    universe = meta_and_ctxs[0]['universe']
    ctxs = meta_and_ctxs[1]

    markets = []
    for asset, ctx in zip(universe, ctxs):
        name = asset['name']
        vol = float(ctx.get('dayNtlVlm', 0.0))
        funding = float(ctx.get('funding', 0.0)) # hourly
        funding_apr = funding * 24 * 365 * 100
        oi = float(ctx.get('openInterest', 0.0))
        mark_px = float(ctx.get('markPx', 0.0))
        prev_px = float(ctx.get('prevDayPx', mark_px))
        change_24h = ((mark_px - prev_px) / prev_px * 100.0) if prev_px > 0 else 0.0
        oi_usd = oi * mark_px
        markets.append({
            'coin': name,
            'volume_24h': vol,
            'funding_apr': funding_apr,
            'oi_usd': oi_usd,
            'mark_px': mark_px,
            'change_24h': change_24h,
            'max_leverage': asset.get('maxLeverage', 10),
        })

    markets.sort(key=lambda x: x['volume_24h'], reverse=True)
    top50 = markets[:50]

    print(f"Scanned {len(markets)} markets. Top 50 volume range: ${top50[-1]['volume_24h']/1e6:.1f}M - ${top50[0]['volume_24h']/1e6:.1f}M")

    # Classify candidates:
    # 1. Negative funding short squeeze (negative funding + positive 24h momentum) -> LONG
    # 2. Extreme positive funding carry fade (high positive funding > 50% APR) -> SHORT
    # 3. High volume trend breakouts
    candidates = []

    for m in top50:
        c = m['coin']
        vol = m['volume_24h']
        f_apr = m['funding_apr']
        chg = m['change_24h']
        px = m['mark_px']
        oi_val = m['oi_usd']

        # Determine bias and score
        bias = "NEUTRAL"
        score = 50.0
        rationale = ""
        category = "TREND"

        if f_apr < -20.0 and chg > 1.0:
            bias = "LONG"
            score = 88.0 + min(10.0, abs(f_apr)/10.0)
            category = "SHORT_SQUEEZE"
            rationale = f"Negative funding squeeze: funding at {f_apr:.1f}% APR with +{chg:.1f}% 24h momentum. Shorts paying longs."
        elif f_apr > 60.0:
            bias = "SHORT"
            score = 85.0 + min(10.0, f_apr/20.0)
            category = "FUNDING_FADE"
            rationale = f"Overheated long carry: funding at +{f_apr:.1f}% APR indicates crowded retail longs ripe for mean reversion."
        elif chg > 4.0 and vol > 10_000_000:
            bias = "LONG"
            score = 80.0 + min(10.0, chg)
            category = "MOMENTUM"
            rationale = f"Bullish momentum expansion: +{chg:.1f}% 24h gain with strong liquidity (${vol/1e6:.1f}M 24h vol)."
        elif chg < -4.0 and vol > 10_000_000:
            bias = "SHORT"
            score = 80.0 + min(10.0, abs(chg))
            category = "BREAKDOWN"
            rationale = f"Bearish momentum breakdown: {chg:.1f}% 24h decline with heavy selling volume (${vol/1e6:.1f}M)."
        elif f_apr < -5.0:
            bias = "LONG"
            score = 75.0 + abs(f_apr)
            category = "FUNDING_DISCOUNT"
            rationale = f"Discounted funding ({f_apr:.1f}% APR) providing positive carry for longs on high-volume asset."
        elif chg > 1.5:
            bias = "LONG"
            score = 72.0 + chg
            category = "TREND"
            rationale = f"Steady upward drift (+{chg:.1f}% 24h) with healthy volume (${vol/1e6:.1f}M)."
        elif chg < -1.5:
            bias = "SHORT"
            score = 72.0 + abs(chg)
            category = "TREND"
            rationale = f"Steady downward drift ({chg:.1f}% 24h) with sustained selling pressure."

        if bias != "NEUTRAL":
            candidates.append({
                'coin': c,
                'bias': bias,
                'score': round(score, 1),
                'mark_price': px,
                'change_24h': round(chg, 2),
                'volume_24h_usd': round(vol, 2),
                'funding_apr_pct': round(f_apr, 2),
                'open_interest_usd': round(oi_val, 2),
                'category': category,
                'rationale': rationale,
                'max_leverage': m['max_leverage'],
            })

    # Select top 5 Longs and top 5 Shorts
    longs = sorted([c for c in candidates if c['bias'] == 'LONG'], key=lambda x: x['score'], reverse=True)[:5]
    shorts = sorted([c for c in candidates if c['bias'] == 'SHORT'], key=lambda x: x['score'], reverse=True)[:5]
    top10 = longs + shorts
    top10.sort(key=lambda x: x['score'], reverse=True)

    # Save to bridge/prospects.json
    out_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scan_cycle": 3,
        "total_scanned": len(markets),
        "top_50_analyzed": len(top50),
        "prospects": top10,
    }

    with open("/home/jooshoo/Desktop/hyprliquid/bridge/prospects.json", "w") as f:
        json.dump(out_data, f, indent=2)

    print(f"Successfully wrote {len(top10)} top prospects to bridge/prospects.json:")
    for p in top10:
        print(f"  [{p['bias']}] {p['coin']} (Score: {p['score']}) - {p['rationale']}")

if __name__ == "__main__":
    run_prospector_scan()
