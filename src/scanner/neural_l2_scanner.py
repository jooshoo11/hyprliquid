"""
Neural L2 Market Scanner & Microstructure Reasoning Engine (src/scanner/neural_l2_scanner.py)

Fully harnesses the Pixel 9's onboard AI chip (Tensor G4) + live WebSockets:
1. Live L2 Order Book Surveillance:
   - Monitors live bids, asks, depth walls, and Order Book Imbalance (OBI) via live_ws_feed.
2. On-Device Neural Synthesis:
   - Feeds live L2 order book depth, fair value micro-price, and funding rates into
     the on-device Qwen 2.5 1.5B model on http://127.0.0.1:8081/v1.
   - The on-device neural model analyzes depth walls and order flow to generate
     high-conviction trade setups with deep quantitative reasoning.
3. Live Cockpit Output:
   - Atomically updates bridge/prospects.json for autonomous AutoTrader execution.
   - Streams neural microstructure insights directly to the Web Cockpit.
"""

import os
import sys
import json
import time
import threading
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.scanner.live_ws_feed import live_feed
from src.scanner.mcp_client import HyperliquidInfoClient
from src.utils.pixel_ai import pixel_ai

PROSPECTS_PATH = os.path.join(REPO_ROOT, "bridge", "prospects.json")
SENTINEL_LOG_PATH = os.path.join(REPO_ROOT, "bridge", "sentinel.log")


class NeuralL2Scanner:
    """
    On-device AI engine scanning live markets and reasoning over streaming L2 order books.
    """

    def __init__(self, scan_interval: float = 60.0):
        self.scan_interval = scan_interval
        self.info_client = HyperliquidInfoClient()
        self.live_feed = live_feed
        self.pixel_ai = pixel_ai

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.last_scan_time: float = 0.0

    def log_sentinel_thought(self, coin: str, thought: str, category: str = "[NEURAL L2]") -> None:
        """Append an AI reasoning event to bridge/sentinel.log for the dashboard."""
        try:
            time_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
            line = f"{time_str} | {category} | {coin} | {thought}\n"
            os.makedirs(os.path.dirname(SENTINEL_LOG_PATH), exist_ok=True)
            with open(SENTINEL_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def scan_and_reason(self) -> List[Dict[str, Any]]:
        """
        Scan live markets, retrieve streaming L2 depth, and formulate neural convictions.
        """
        # Ensure live feed is running
        self.live_feed.start()

        meta, contexts = self.info_client.get_meta_and_asset_ctxs()
        if not meta or not contexts:
            return []

        universe = meta.get("universe", [])
        ws_prices = self.live_feed.get_all_prices()

        candidates = []
        for u, ctx in zip(universe, contexts):
            coin = u.get("name", "").upper()
            px = ws_prices.get(coin, float(ctx.get("oraclePx", 0.0)))
            prev_px = float(ctx.get("prevDayPx", 0.0))
            vol_24h = float(ctx.get("dayNtlVlm", 0.0))
            funding = float(ctx.get("funding", 0.0))
            funding_apr = funding * 24 * 365 * 100.0
            chg_24h = ((px - prev_px) / prev_px * 100.0) if prev_px > 0 else 0.0

            if px > 0 and vol_24h > 1_000_000:
                candidates.append({
                    "coin": coin,
                    "price": px,
                    "vol_24h": vol_24h,
                    "funding_apr": funding_apr,
                    "change_24h": chg_24h,
                    "max_leverage": float(u.get("maxLeverage", 10.0)),
                })

        candidates.sort(key=lambda x: x["vol_24h"], reverse=True)
        top_candidates = candidates[:15]

        # Dynamically subscribe top candidates to live L2 WebSocket stream
        for cand in top_candidates:
            self.live_feed.subscribe_coin(cand["coin"])

        # Allow brief tick for L2 stream to register
        time.sleep(1.0)

        prospects = []
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Evaluate candidates with live L2 order book + Onboard AI
        for idx, cand in enumerate(top_candidates[:8]):
            coin = cand["coin"]
            px = cand["price"]
            micro = self.live_feed.get_microstructure(coin) or {}

            obi = micro.get("obi", 0.0)
            spread_bps = micro.get("spread_bps", 1.5)
            bid_depth = micro.get("bid_depth_usd", 0.0)
            ask_depth = micro.get("ask_depth_usd", 0.0)
            micro_px = micro.get("micro_price", px)
            funding_apr = cand["funding_apr"]
            chg_24h = cand["change_24h"]
            vol_m = cand["vol_24h"] / 1e6

            max_lev = float(cand.get("max_leverage", 10.0))

            # Query Onboard AI (llama-server) for top priority setups (limit to top 3 to keep scan latency under 15s)
            neural_setup = None
            if idx < 3 and self.pixel_ai.is_online():
                prompt = (
                    f"Analyze live L2 microstructure for perpetual contract:\n"
                    f"Coin: {coin}\n"
                    f"Mark Price: ${px:,.4f} | Micro-Price: ${micro_px:,.4f}\n"
                    f"Order Book Imbalance (OBI): {obi:+.3f} (Bid Depth: ${bid_depth:,.0f} vs Ask Depth: ${ask_depth:,.0f})\n"
                    f"Spread: {spread_bps:.1f} bps | Funding APR: {funding_apr:+.1f}%\n"
                    f"24h Vol: ${vol_m:.1f}M (+{chg_24h:+.1f}%)\n"
                    f"Output JSON with bias (LONG/SHORT), conviction_score (50-100), target_entry, stop_loss, take_profit, reason."
                )
                system_prompt = (
                    "You are the Pixel 9 on-device neural trading analyst evaluating real-time L2 order book dynamics.\n"
                    "Respond STRICTLY in valid JSON matching:\n"
                    '{"bias": "LONG"|"SHORT", "conviction_score": 85, "target_entry": 0.0, "stop_loss": 0.0, "take_profit": 0.0, "reason": "1 concise sentence"}'
                )
                neural_setup = self.pixel_ai.query(prompt, system_prompt, max_tokens=140)

            if neural_setup and isinstance(neural_setup, dict) and "bias" in neural_setup:
                bias = str(neural_setup.get("bias", "LONG")).upper()
                score = int(neural_setup.get("conviction_score", 85))
                reason = neural_setup.get("reason", f"L2 OBI {obi:+.2f} with ${vol_m:.1f}M volume")

                target_entry = float(neural_setup.get("target_entry") or (px * (0.998 if bias == "LONG" else 1.002)))
                stop_loss = float(neural_setup.get("stop_loss") or (px * (0.990 if bias == "LONG" else 1.010)))
                take_profit = float(neural_setup.get("take_profit") or (px * (1.025 if bias == "LONG" else 0.975)))

                self.log_sentinel_thought(coin, f"Onboard AI Reasoned: {bias} (Score: {score}) — {reason}")
            else:
                # Deterministic High-Precision L2 Quant Rule
                is_long = (obi > 0.15 and funding_apr < 25.0) or (funding_apr < -20.0)
                bias = "LONG" if is_long else "SHORT"
                score = 88 if abs(obi) > 0.25 else 82
                target_entry = round(px * (0.998 if bias == "LONG" else 1.002), 4)
                stop_loss = round(px * (0.990 if bias == "LONG" else 1.010), 4)
                take_profit = round(px * (1.025 if bias == "LONG" else 0.975), 4)
                reason = f"L2 Order Flow: OBI {obi:+.2f} ({'Bid Heavy' if obi>0 else 'Ask Heavy'}) on ${vol_m:.1f}M vol; Funding APR {funding_apr:+.1f}%."

            prospect_entry = {
                "coin": coin,
                "bias": bias,
                "conviction_score": score,
                "max_leverage": max_lev,
                "target_entry": target_entry,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "strategy": "OrderBookImbalance" if abs(obi) > 0.20 else ("ShortSqueezeIgnition" if funding_apr < -20.0 else "TrendContinuationSMC"),
                "reason": reason,
                "rationale": reason,
                "funding_apr": round(funding_apr, 2),
                "obi": obi,
                "spread_bps": spread_bps,
                "updated_at": now_iso,
            }
            prospects.append(prospect_entry)

        # Sort by conviction score
        prospects.sort(key=lambda x: x["conviction_score"], reverse=True)

        # Atomically save to bridge/prospects.json
        if prospects:
            try:
                tmp = f"{PROSPECTS_PATH}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"prospects": prospects, "timestamp": time.time(), "engine": "Pixel9_Neural_L2"}, f, indent=2)
                os.replace(tmp, PROSPECTS_PATH)
            except Exception:
                pass

        self.last_scan_time = time.time()
        return prospects

    def loop(self) -> None:
        """Background continuous neural scanning loop."""
        self._running = True
        while self._running:
            try:
                self.scan_and_reason()
            except Exception:
                pass
            time.sleep(self.scan_interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self.loop, daemon=True, name="NeuralL2Scanner")
        self._thread.start()

    def stop(self) -> None:
        self._running = False


# Shared singleton instance
neural_scanner = NeuralL2Scanner()
