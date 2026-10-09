"""
Mid-Frequency Market Regime & Playbook Governor (src/risk/regime_governor.py)

Operates on a 5-15 minute cadence to govern system-wide execution playbooks:
Instead of running continuous per-coin LLM inference, it synthesizes macro market breadth,
BTC/ETH delta, aggregate funding dispersion, and tape flow into one of three execution regimes:
1. MOMENTUM_EXPANSION:
   - Widen take-profits (3.5x - 4.0x RR), disable counter-trend fades, allow full position sizing.
2. LIQUIDATION_CHOP:
   - Tighten stops, enable counter-trend fading, halve margin allocation (0.5x).
3. CASCADE_RISK (Flight-to-Cash):
   - Pause all new entries, move existing stops to breakeven immediately.
"""

import os
import sys
import json
import time
import threading
from typing import Dict, Any, List, Optional
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.scanner.live_ws_feed import live_feed
from src.scanner.mcp_client import HyperliquidInfoClient
from src.utils.pixel_ai import pixel_ai

MARKET_REGIME_PATH = os.path.join(REPO_ROOT, "bridge", "market_regime.json")


class MarketRegimeGovernor:
    """
    On-device governor running every 5-15 mins to modulate system risk parameters and strategy playbooks.
    """

    def __init__(self, cycle_interval: float = 300.0):
        self.cycle_interval = cycle_interval
        self.info_client = HyperliquidInfoClient()
        self.live_feed = live_feed
        self.pixel_ai = pixel_ai

        self.current_regime: str = "LIQUIDATION_CHOP"
        self.last_governor_time: float = 0.0
        self.playbook_params: Dict[str, Any] = {
            "regime": "LIQUIDATION_CHOP",
            "tp_rr_ratio": 2.5,
            "margin_sizing_mult": 0.7,
            "allow_counter_trend": True,
            "pause_entries": False,
            "ratchet_breakeven_all": False,
            "rationale": "Initial baseline initialization.",
            "confidence": 85,
            "timestamp": time.time(),
        }

        self._running = False
        self._thread: Optional[threading.Thread] = None

    def evaluate_regime(self) -> Dict[str, Any]:
        """
        Evaluate aggregate cross-asset metrics and query on-device LLM to classify market regime.
        """
        now = time.time()
        self.last_governor_time = now

        # Aggregate macro data
        breadth_pct = self.live_feed.macro_metrics.get("hl_breadth_pct", 50.0)
        btc_5m = self.live_feed.macro_metrics.get("btc_ret_5m", 0.0)
        eth_5m = self.live_feed.macro_metrics.get("eth_ret_5m", 0.0)
        btc_flow = self.live_feed.get_flow_metrics("BTC")
        btc_cvd_5m = btc_flow.get("cvd_5m", 0.0)

        meta, contexts = self.info_client.get_meta_and_asset_ctxs()
        total_vol = 0.0
        fundings = []
        if contexts:
            for ctx in contexts:
                total_vol += float(ctx.get("dayNtlVlm", 0.0))
                fundings.append(float(ctx.get("funding", 0.0)) * 24 * 365 * 100.0)

        avg_fund = (sum(fundings) / len(fundings)) if fundings else 10.0
        neg_count = sum(1 for f in fundings if f < -15.0)
        pos_count = sum(1 for f in fundings if f > 40.0)
        total_vol_m = total_vol / 1e6

        dense_macro_vector = (
            f"[MACRO BREADTH] BREADTH: {breadth_pct:.1f}% BULL | BTC_5M: {btc_5m:+.2f}% | ETH_5M: {eth_5m:+.2f}%\n"
            f"[DERIV AGGREGATE] AVG_FUND: {avg_fund:+.1f}% APR (Neg Extremes: {neg_count}, High Extremes: {pos_count})\n"
            f"[TAPE AGGREGATE] BTC_CVD_5M: ${btc_cvd_5m:,.0f} | 24H_VOL: ${total_vol_m:,.0f}M"
        )

        regime_result = None
        if self.pixel_ai.is_online():
            system_prompt = (
                "You are the Chief Quantitative Risk Officer on the Pixel 9 Tensor G4.\n"
                "Classify the prevailing crypto perpetual market into exactly one of three operational regimes:\n"
                '- "MOMENTUM_EXPANSION": Broad accumulation/breakout; trend continuation active.\n'
                '- "LIQUIDATION_CHOP": Range-bound, mean-reverting, liquidation sweeps on both sides.\n'
                '- "CASCADE_RISK": Severe adverse liquidation cascade, sharp market flush, extreme contagion.\n'
                "Respond STRICTLY in valid JSON matching:\n"
                '{"regime": "MOMENTUM_EXPANSION"|"LIQUIDATION_CHOP"|"CASCADE_RISK", "confidence": 85, "rationale": "1 concise sentence"}'
            )
            res = self.pixel_ai.query(dense_macro_vector, system_prompt, max_tokens=100)
            if res and isinstance(res, dict) and "regime" in res:
                regime_result = res

        # Deterministic fallback if LLM is offline or unparsable
        if not regime_result:
            if btc_5m < -1.5 or (breadth_pct < 25.0 and btc_cvd_5m < -500_000):
                regime_name = "CASCADE_RISK"
                rationale = "Severe negative breadth and heavy BTC liquidation tape detected."
            elif (breadth_pct >= 65.0 and btc_5m > 0.3) or (breadth_pct <= 35.0 and btc_5m < -0.3):
                regime_name = "MOMENTUM_EXPANSION"
                rationale = "Coordinated market breadth and directional trend continuation active."
            else:
                regime_name = "LIQUIDATION_CHOP"
                rationale = "Mean-reverting dispersion with balanced market breadth."
            regime_result = {"regime": regime_name, "confidence": 85, "rationale": rationale}

        regime = regime_result.get("regime", "LIQUIDATION_CHOP")
        rationale = regime_result.get("rationale", "")
        conf = int(regime_result.get("confidence", 85))

        # Modulate Playbook Parameters according to user spec
        if regime == "MOMENTUM_EXPANSION":
            playbook = {
                "regime": "MOMENTUM_EXPANSION",
                "tp_rr_ratio": 3.8,  # Widen take-profits
                "margin_sizing_mult": 1.1,  # Full position sizing
                "allow_counter_trend": False,  # Disable mean-reversion fades
                "pause_entries": False,
                "ratchet_breakeven_all": False,
                "rationale": rationale,
                "confidence": conf,
                "timestamp": now,
            }
        elif regime == "CASCADE_RISK":
            playbook = {
                "regime": "CASCADE_RISK",
                "tp_rr_ratio": 2.0,
                "margin_sizing_mult": 0.0,
                "allow_counter_trend": False,
                "pause_entries": True,  # Pause all new entries
                "ratchet_breakeven_all": True,  # Move existing stops to breakeven immediately
                "rationale": rationale,
                "confidence": conf,
                "timestamp": now,
            }
        else:  # LIQUIDATION_CHOP
            playbook = {
                "regime": "LIQUIDATION_CHOP",
                "tp_rr_ratio": 2.2,  # Tighten targets
                "margin_sizing_mult": 0.5,  # Halve margin allocation
                "allow_counter_trend": True,  # Enable counter-trend fading
                "pause_entries": False,
                "ratchet_breakeven_all": False,
                "rationale": rationale,
                "confidence": conf,
                "timestamp": now,
            }

        self.current_regime = regime
        self.playbook_params = playbook

        # Persist to bridge/market_regime.json
        try:
            existing = {}
            if os.path.exists(MARKET_REGIME_PATH):
                with open(MARKET_REGIME_PATH, "r") as f:
                    existing = json.load(f)
            existing.update(playbook)
            existing["macro_vector"] = dense_macro_vector
            tmp_p = f"{MARKET_REGIME_PATH}.tmp"
            with open(tmp_p, "w") as f:
                json.dump(existing, f, indent=2)
            os.replace(tmp_p, MARKET_REGIME_PATH)
        except Exception:
            pass

        return playbook

    def get_playbook(self) -> Dict[str, Any]:
        """Return the active playbook parameters."""
        return dict(self.playbook_params)

    def start(self) -> None:
        """Start mid-frequency background governor loop."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True

        def _loop():
            # Initial run after 5s startup
            time.sleep(5)
            while self._running:
                try:
                    self.evaluate_regime()
                except Exception:
                    pass
                time.sleep(self.cycle_interval)

        self._thread = threading.Thread(target=_loop, daemon=True, name="RegimeGovernor")
        self._thread.start()

    def stop(self) -> None:
        self._running = False


# Shared singleton instance
regime_governor = MarketRegimeGovernor(cycle_interval=300.0)  # Every 5 minutes
