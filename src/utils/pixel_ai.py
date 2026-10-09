"""
Pixel 9 Onboard AI & Neural Inference Engine (src/utils/pixel_ai.py)

Dedicated on-device AI runtime for Google Pixel 9 (Tensor G4 SoC):
1. Hardware Acceleration:
   - Connects to local llama-server on http://127.0.0.1:8081/v1 powered by
     ARMv9 Cortex-X4 / Cortex-A720 performance cores & Mali-G715 Vulkan acceleration.
   - Zero cloud tokens, zero network latency, 100% offline, zero rate limits.
2. Built-in Deterministic Quant Fallback:
   - If llama-server is offline, instantly executes high-precision quantitative logic
     (<1ms latency) for Risk Sentinel audits, Market Regime categorization, and Prospect rankings.
3. Structured Output Enforcement:
   - Guarantees clean JSON output schemas for downstream automated execution.
"""

import os
import json
import time
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, Tuple

LOCAL_SERVER_URL = os.getenv("PIXEL_AI_URL", "http://127.0.0.1:8081/v1")


class PixelOnboardAI:
    """
    On-device AI interface leveraging Pixel 9 Tensor G4 hardware.
    """

    def __init__(self, endpoint_url: str = LOCAL_SERVER_URL):
        self.endpoint_url = endpoint_url.rstrip("/")
        self.chat_url = f"{self.endpoint_url}/chat/completions"
        self._last_health_check: float = 0.0
        self._is_online: bool = False

    def is_online(self) -> bool:
        """Check if local llama-server is currently active on localhost."""
        now = time.time()
        if (now - self._last_health_check) < 15.0:
            return self._is_online

        self._last_health_check = now
        try:
            req = urllib.request.Request(f"{self.endpoint_url}/models", method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as res:
                self._is_online = (res.status == 200)
        except Exception:
            self._is_online = False

        return self._is_online

    def get_hardware_info(self) -> Dict[str, Any]:
        """Return Pixel 9 Tensor G4 hardware specifications & status."""
        return {
            "device": "Google Pixel 9",
            "soc": "Google Tensor G4",
            "cores": 8,
            "core_layout": "1x Cortex-X4 (3.1 GHz), 3x Cortex-A720 (2.6 GHz), 4x Cortex-A520 (1.95 GHz)",
            "gpu": "Mali-G715 (Vulkan 1.3 / OpenCL)",
            "engine_status": "ONLINE (Local LLM)" if self.is_online() else "STANDBY (Fast Deterministic Quant)",
            "endpoint": self.endpoint_url,
            "token_cost": "$0.00 (Unlimited On-Device)",
        }

    def query(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute an on-device inference call with JSON parsing.
        """
        if not self.is_online():
            return None

        payload = {
            "messages": [
                {"role": "system", "content": system_prompt + "\nYou MUST reply with valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        try:
            t0 = time.time()
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.chat_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12.0) as res:
                res_body = json.loads(res.read().decode("utf-8"))
                latency_ms = (time.time() - t0) * 1000.0

                content = res_body["choices"][0]["message"]["content"]
                # Clean code blocks
                clean = content.strip()
                if clean.startswith("```"):
                    lines = clean.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    clean = "\n".join(lines).strip()

                parsed = json.loads(clean)
                parsed["_latency_ms"] = round(latency_ms, 1)
                parsed["_provider"] = "pixel_onboard_llm"
                return parsed
        except Exception:
            return None

    def audit_sentinel_risk(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Audit live positions and portfolio risk.
        Uses local llama-server if running, or high-precision deterministic quant logic.
        """
        positions = snapshot.get("positions", [])
        equity = float(snapshot.get("equity", 100.0))
        open_orders = snapshot.get("open_orders", [])
        net_unrealized = float(snapshot.get("net_unrealized", 0.0))

        # 1. Attempt local on-device LLM inference first
        if self.is_online():
            system_prompt = (
                "You are an elite quantitative risk sentinel auditing an autonomous Hyperliquid trading bot.\n"
                "Respond STRICTLY with raw JSON matching this schema:\n"
                "{\n"
                '  "status": "HEALTHY" | "CAUTION" | "ALERT" | "CRITICAL",\n'
                '  "action": "NONE" | "CANCEL_ORPHANS" | "CLOSE_POSITION" | "FLATTEN_ALL",\n'
                '  "target_coins": ["COIN1"],\n'
                '  "reason": "1 concise sentence explaining risk decision",\n'
                '  "confidence": 0-100\n'
                "}"
            )
            user_prompt = f"Portfolio state: {json.dumps(snapshot)}"
            res = self.query(user_prompt, system_prompt, max_tokens=256)
            if res and isinstance(res, dict) and "status" in res:
                return res

        # 2. High-Precision Deterministic Quant Logic (<1ms, zero token cost)
        t0 = time.time()
        active_coins = {p.get("coin", "").upper() for p in positions}

        # Check orphaned orders
        orphaned = []
        for o in open_orders:
            coin = o.get("coin", "").upper()
            if coin and coin not in active_coins:
                orphaned.append(coin)

        if orphaned:
            return {
                "status": "CAUTION",
                "action": "CANCEL_ORPHANS",
                "target_coins": list(set(orphaned)),
                "reason": f"Detected {len(orphaned)} orphaned orders without matching open positions.",
                "confidence": 99,
                "_provider": "pixel_onboard_quant",
                "_latency_ms": round((time.time() - t0) * 1000, 2),
            }

        # Check critical portfolio drawdown
        if equity > 0 and (net_unrealized / equity) <= -0.15:
            return {
                "status": "CRITICAL",
                "action": "FLATTEN_ALL",
                "target_coins": list(active_coins),
                "reason": f"Critical portfolio drawdown: Floating P&L (${net_unrealized:.2f}) exceeds 15% of equity.",
                "confidence": 98,
                "_provider": "pixel_onboard_quant",
                "_latency_ms": round((time.time() - t0) * 1000, 2),
            }

        # Check individual position health
        for p in positions:
            coin = p.get("coin", "").upper()
            roi = float(p.get("roi_pct", 0.0))
            if roi <= -25.0:  # Severe loss on margin
                return {
                    "status": "ALERT",
                    "action": "CLOSE_POSITION",
                    "target_coins": [coin],
                    "reason": f"Position {coin} breached risk threshold with {roi:.1f}% ROI on margin.",
                    "confidence": 95,
                    "_provider": "pixel_onboard_quant",
                    "_latency_ms": round((time.time() - t0) * 1000, 2),
                }

        return {
            "status": "HEALTHY",
            "action": "NONE",
            "target_coins": [],
            "reason": f"Nominal operations across {len(positions)} open positions. Portfolio within safe limits.",
            "confidence": 97,
            "_provider": "pixel_onboard_quant",
            "_latency_ms": round((time.time() - t0) * 1000, 2),
        }

    def audit_position_orderflow(
        self,
        position: Dict[str, Any],
        microstructure: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Actively monitor an open position in real time using Tensor G4 onboard AI + L2 order book metrics.
        Evaluates whether to CONFIRM_HOLD, REJECT_EXIT (cut early on wall collapse), or TIGHTEN_STOP.
        """
        coin = str(position.get("coin", "")).upper()
        side = str(position.get("side", "LONG")).upper()
        entry_px = float(position.get("entry_price", 0.0))
        mark_px = float(position.get("mark_price", entry_px))
        roi_pct = float(position.get("roi_pct", 0.0))
        leverage = float(position.get("leverage", 10.0))
        margin = float(position.get("margin", 10.0))
        sl = float(position.get("stop_loss", 0.0))
        tp = float(position.get("take_profit", 0.0))

        micro = microstructure or {}
        obi = float(micro.get("obi", 0.0))
        bid_depth = float(micro.get("bid_depth_usd", 0.0))
        ask_depth = float(micro.get("ask_depth_usd", 0.0))
        micro_px = float(micro.get("micro_price", mark_px))
        spread_bps = float(micro.get("spread_bps", 1.0))
        imbalance_label = micro.get("imbalance_label", "BALANCED")

        price_pct = ((mark_px - entry_px) / entry_px * 100.0) if side == "LONG" else ((entry_px - mark_px) / entry_px * 100.0)

        # 1. Attempt on-device LLM reasoning first
        if self.is_online():
            system_prompt = (
                "You are the Pixel 9 on-device real-time trade sentry.\n"
                "You evaluate an open trade to decide if the bot should stay (CONFIRM_HOLD),\n"
                "cut early due to adverse order flow wall collapse on a LOSING trade (REJECT_EXIT),\n"
                "or tighten stop to protect a WINNING trade (TIGHTEN_STOP).\n"
                "CRITICAL RULES:\n"
                "1. For profitable positions (ROI > 0%), NEVER suggest REJECT_EXIT.\n"
                "2. For fresh trades where price move < +0.60%, ALWAYS CONFIRM_HOLD to give the trade breathing room to run toward Take Profit (+1.8% to +3.5%).\n"
                "3. Never tighten a stop within 0.8% of the mark price.\n"
                "Respond STRICTLY in valid JSON matching:\n"
                '{"verdict": "CONFIRM_HOLD"|"REJECT_EXIT"|"TIGHTEN_STOP", "confidence": 85, "reason": "1 concise sentence", "tighten_stop_price": 0.0}'
            )
            user_prompt = (
                f"Open Trade: {side} {coin} ({leverage:.0f}x Lev)\n"
                f"Entry: ${entry_px:,.4f} | Current Mark: ${mark_px:,.4f} | Price Move: {price_pct:+.2f}% | Margin ROI: {roi_pct:+.2f}%\n"
                f"L2 Microstructure:\n"
                f"- Order Book Imbalance (OBI): {obi:+.3f} ({imbalance_label})\n"
                f"- Bid Depth: ${bid_depth:,.0f} vs Ask Depth: ${ask_depth:,.0f}\n"
                f"- Micro-Price: ${micro_px:,.4f} | Spread: {spread_bps:.1f} bps\n"
                f"Should the system CONFIRM_HOLD, REJECT_EXIT, or TIGHTEN_STOP?"
            )
            res = self.query(user_prompt, system_prompt, max_tokens=150)
            if res and isinstance(res, dict) and "verdict" in res:
                v = res.get("verdict")
                # Safety guard 1: never panic-exit winning trades
                if price_pct >= 0.0 and v == "REJECT_EXIT":
                    v = "TIGHTEN_STOP"
                    res["verdict"] = v

                # Safety guard 2: early trade breathing room (price_pct < 0.60%)
                if 0.0 <= price_pct < 0.60 and v == "TIGHTEN_STOP":
                    res["verdict"] = "CONFIRM_HOLD"
                    res["tighten_stop_price"] = 0.0
                    res["reason"] = f"Breathing room active (+{price_pct:.2f}% price / +{roi_pct:.1f}% ROI): Giving trade room to hit Take-Profit (+1.8% to +3.5%)."
                    return res

                # Safety guard 3: mathematical sound trailing stop calculation
                if v == "TIGHTEN_STOP":
                    if side == "LONG":
                        safe_sl = round(entry_px * 1.0020, 4) if price_pct >= 0.60 else round(mark_px * 0.990, 4)
                        if price_pct >= 1.20:
                            safe_sl = max(safe_sl, round(mark_px * 0.992, 4))
                        # Invariant: Must be strictly below mark_px with at least 0.4% buffer
                        safe_sl = min(safe_sl, round(mark_px * 0.996, 4))
                        res["tighten_stop_price"] = safe_sl
                        res["reason"] = f"Securing gains (+{roi_pct:.1f}% ROI | +{price_pct:.2f}% price): Ratcheted protective stop to ${safe_sl:,.4f} with breathing room."
                    else:
                        safe_sl = round(entry_px * 0.9980, 4) if price_pct >= 0.60 else round(mark_px * 1.010, 4)
                        if price_pct >= 1.20:
                            safe_sl = min(safe_sl, round(mark_px * 1.008, 4))
                        # Invariant: Must be strictly above mark_px with at least 0.4% buffer
                        safe_sl = max(safe_sl, round(mark_px * 1.004, 4))
                        res["tighten_stop_price"] = safe_sl
                        res["reason"] = f"Securing gains (+{roi_pct:.1f}% ROI | +{price_pct:.2f}% price): Ratcheted protective stop to ${safe_sl:,.4f} with breathing room."
                return res

        # 2. Deterministic High-Precision Order Flow Guardian (<1ms fallback)
        t0 = time.time()
        if side == "LONG":
            # REJECT_EXIT: Only for LOSING positions where order flow wall has broken down
            if roi_pct < -3.0 and ((obi < -0.40 and ask_depth > 1.8 * max(1.0, bid_depth)) or obi < -0.60):
                return {
                    "verdict": "REJECT_EXIT",
                    "confidence": 90,
                    "reason": f"Adverse ask wall collapse on losing Long ({roi_pct:.1f}% ROI): OBI {obi:+.2f} with ask depth ${ask_depth:,.0f} > bid depth; cut early to preserve margin.",
                    "tighten_stop_price": 0.0,
                    "_provider": "pixel_onboard_quant",
                }
            # TIGHTEN_STOP: Only for well-developed winners (price_pct >= 0.60%) where resistance wall is forming
            elif price_pct >= 0.60 and (obi < -0.20 or ask_depth > 1.5 * max(1.0, bid_depth)):
                be_sl = round(entry_px * 1.0020, 4)  # Net breakeven covering fees + locked profit
                if price_pct >= 1.20:
                    tighter = max(be_sl, round(mark_px * 0.992, 4))  # 0.8% runner trailing buffer
                else:
                    tighter = be_sl
                # Hard invariant: must remain at least 0.4% below mark_px
                tighter = min(tighter, round(mark_px * 0.996, 4))
                return {
                    "verdict": "TIGHTEN_STOP",
                    "confidence": 85,
                    "reason": f"Securing gains (+{roi_pct:.1f}% ROI | +{price_pct:.2f}% price): Ask resistance detected ({obi:+.2f}); ratcheted stop to ${tighter:,.4f} with breathing room.",
                    "tighten_stop_price": tighter,
                    "_provider": "pixel_onboard_quant",
                }
            else:
                return {
                    "verdict": "CONFIRM_HOLD",
                    "confidence": 88,
                    "reason": f"Order flow intact: Bid depth ${bid_depth:,.0f} and OBI {obi:+.2f} support Long continuation ({roi_pct:+.1f}% ROI | {price_pct:+.2f}% price).",
                    "tighten_stop_price": 0.0,
                    "_provider": "pixel_onboard_quant",
                }
        else:  # SHORT
            # REJECT_EXIT: Only for LOSING positions where bid wall has overwhelmed
            if roi_pct < -3.0 and ((obi > 0.40 and bid_depth > 1.8 * max(1.0, ask_depth)) or obi > 0.60):
                return {
                    "verdict": "REJECT_EXIT",
                    "confidence": 90,
                    "reason": f"Adverse bid wall buildup on losing Short ({roi_pct:.1f}% ROI): OBI {obi:+.2f} with bid depth ${bid_depth:,.0f} > ask depth; cut early to preserve margin.",
                    "tighten_stop_price": 0.0,
                    "_provider": "pixel_onboard_quant",
                }
            # TIGHTEN_STOP: Only for well-developed winners (price_pct >= 0.60%) where downward momentum slows
            elif price_pct >= 0.60 and (obi > 0.20 or bid_depth > 1.5 * max(1.0, ask_depth)):
                be_sl = round(entry_px * 0.9980, 4)  # Net breakeven covering fees
                if price_pct >= 1.20:
                    tighter = min(be_sl, round(mark_px * 1.008, 4))  # 0.8% runner trailing buffer
                else:
                    tighter = be_sl
                # Hard invariant: must remain at least 0.4% above mark_px
                tighter = max(tighter, round(mark_px * 1.004, 4))
                return {
                    "verdict": "TIGHTEN_STOP",
                    "confidence": 85,
                    "reason": f"Securing gains (+{roi_pct:.1f}% ROI | +{price_pct:.2f}% price): Bid support detected ({obi:+.2f}); ratcheted stop to ${tighter:,.4f} with breathing room.",
                    "tighten_stop_price": tighter,
                    "_provider": "pixel_onboard_quant",
                }
            else:
                return {
                    "verdict": "CONFIRM_HOLD",
                    "confidence": 88,
                    "reason": f"Order flow intact: Ask pressure and OBI {obi:+.2f} favor continued Short momentum ({roi_pct:+.1f}% ROI | {price_pct:+.2f}% price).",
                    "tighten_stop_price": 0.0,
                    "_provider": "pixel_onboard_quant",
                }


# Global singleton instance
pixel_ai = PixelOnboardAI()
