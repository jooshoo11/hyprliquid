"""
Hyperliquid Market Intelligence & Sector Radar (src/scanner/hl_intelligence.py)

Specialized intelligence engine dedicated exclusively to Hyperliquid's 234+ perpetuals:
1. Sector Categorization & Momentum Rotation (Memes, AI, Alt-L1s, DeFi, Majors).
2. Hyperliquid Symbol Normalization (e.g., maps PEPE -> kPEPE, BONK -> kBONK).
3. New Perpetual Listing Watcher (detects newly listed coins on Hyperliquid).
4. Extreme Squeeze & Carry Ranking across all active contracts.
"""

import os
import json
import urllib.request
from typing import Dict, Any, List, Optional
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent / "hl_universe_cache.json"

# Defined Narrative Sectors on Hyperliquid
HL_SECTORS = {
    "MEMES": [
        "DOGE", "WIF", "POPCAT", "GOAT", "MOODENG", "TURBO", "BRETT", 
        "kPEPE", "kBONK", "kSHIB", "MEME", "BOME", "NEIRO"
    ],
    "AI_COMPUTE": [
        "TAO", "RENDER", "NEAR", "FET", "IO", "AKT", "ATH", "WLD", "ARKM"
    ],
    "HIGH_BETA_L1": [
        "SOL", "SUI", "APT", "SEI", "TIA", "AVAX", "INJ", "TON"
    ],
    "DEFI": [
        "AAVE", "UNI", "CRV", "MKR", "PENDLE", "ENA", "LDO", "DYDX", "SNX"
    ],
    "MAJORS": [
        "BTC", "ETH"
    ]
}

# Mapping common external social tickers to Hyperliquid perpetual identifiers
TICKER_TO_HL_MAP = {
    "PEPE": "kPEPE",
    "BONK": "kBONK",
    "SHIB": "kSHIB",
    "FLOKI": "kFLOKI",
    "1000PEPE": "kPEPE",
    "1000BONK": "kBONK",
}


class HyperliquidIntelligence:
    def __init__(self, api_url: str = "https://api.hyperliquid.xyz/info"):
        self.api_url = api_url
        self.universe_cache_path = CACHE_FILE

    def normalize_ticker(self, ticker: str) -> str:
        """Translates external tickers (e.g. from Crypto_Sonar) to Hyperliquid perpetual names."""
        clean = ticker.upper().replace("$", "").strip()
        return TICKER_TO_HL_MAP.get(clean, clean)

    def fetch_meta_and_contexts(self) -> tuple:
        """Fetches complete live metadata and asset contexts from Hyperliquid."""
        try:
            req = urllib.request.Request(
                self.api_url,
                data=json.dumps({"type": "metaAndAssetCtxs"}).encode(),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=6) as res:
                data = json.loads(res.read().decode())
                return data[0]["universe"], data[1]
        except Exception as e:
            return None, None

    def detect_new_listings(self, current_universe_names: List[str]) -> List[str]:
        """Detects if any new perpetuals were added to Hyperliquid since last cache."""
        newly_listed = []
        if self.universe_cache_path.exists():
            try:
                with open(self.universe_cache_path, "r") as f:
                    cached_names = json.load(f)
                newly_listed = [c for c in current_universe_names if c not in cached_names]
            except Exception:
                pass

        # Save snapshot
        try:
            with open(self.universe_cache_path, "w") as f:
                json.dump(current_universe_names, f)
        except Exception:
            pass

        return newly_listed

    def analyze_sector_momentum(self) -> Dict[str, Any]:
        """Calculates sector-wide momentum, volume leadership, and narrative rotations."""
        universe, contexts = self.fetch_meta_and_contexts()
        if not universe or not contexts:
            return {"status": "error", "message": "Failed to fetch Hyperliquid data"}

        current_names = [u["name"] for u in universe]
        new_listings = self.detect_new_listings(current_names)

        # Build quick lookup
        coin_metrics = {}
        for u, ctx in zip(universe, contexts):
            name = u["name"]
            px = float(ctx.get("oraclePx", 0.0))
            prev_px = float(ctx.get("prevDayPx", 0.0))
            chg_24h = ((px - prev_px) / prev_px * 100.0) if prev_px > 0 else 0.0
            vol_24h = float(ctx.get("dayNtlVlm", 0.0))
            funding_apr = float(ctx.get("funding", 0.0)) * 24 * 365 * 100.0

            coin_metrics[name] = {
                "price": px,
                "change_24h": chg_24h,
                "vol_24h": vol_24h,
                "funding_apr": funding_apr
            }

        # Calculate Sector Aggregates
        sector_results = {}
        hottest_sector = None
        max_sector_perf = -999.0

        for sector_name, coins in HL_SECTORS.items():
            active_coins = [c for c in coins if c in coin_metrics]
            if not active_coins:
                continue

            perf_list = [coin_metrics[c]["change_24h"] for c in active_coins]
            vol_total = sum(coin_metrics[c]["vol_24h"] for c in active_coins)
            avg_perf = sum(perf_list) / len(perf_list)

            # Find top performing leader in the sector
            leader = max(active_coins, key=lambda c: coin_metrics[c]["change_24h"])

            sector_results[sector_name] = {
                "active_count": len(active_coins),
                "avg_change_24h": round(avg_perf, 2),
                "total_volume_usd": round(vol_total, 2),
                "leader_coin": leader,
                "leader_change_24h": round(coin_metrics[leader]["change_24h"], 2),
                "rotation_status": "HOT 🔥" if avg_perf > 4.0 else ("COOLING ❄️" if avg_perf < -2.0 else "NEUTRAL")
            }

            if avg_perf > max_sector_perf:
                max_sector_perf = avg_perf
                hottest_sector = sector_name

        # Detect Squeeze Outliers (< -15% APR funding with positive momentum)
        squeeze_outliers = []
        for name, m in coin_metrics.items():
            if m["funding_apr"] < -15.0 and m["change_24h"] > 1.0 and m["vol_24h"] > 1_000_000:
                squeeze_outliers.append({
                    "coin": name,
                    "funding_apr": round(m["funding_apr"], 1),
                    "change_24h": round(m["change_24h"], 2),
                    "vol_24h": round(m["vol_24h"], 2)
                })

        return {
            "status": "success",
            "active_perpetuals": len(current_names),
            "new_listings": new_listings,
            "hottest_sector": hottest_sector,
            "sectors": sector_results,
            "squeeze_alerts": squeeze_outliers
        }
