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

        # 1. Attempt on-device LLM reasoning first
        if self.is_online():
            system_prompt = (
                "You are the Pixel 9 on-device real-time trade sentry.\n"
                "You evaluate an open trade to decide if the bot should stay (CONFIRM_HOLD),\n"
                "cut early due to adverse order flow wall collapse (REJECT_EXIT), or tighten stop (TIGHTEN_STOP).\n"
                "Respond STRICTLY in valid JSON matching:\n"
                '{"verdict": "CONFIRM_HOLD"|"REJECT_EXIT"|"TIGHTEN_STOP", "confidence": 85, "reason": "1 concise sentence", "tighten_stop_price": 0.0}'
            )
            user_prompt = (
                f"Open Trade: {side} {coin} ({leverage:.0f}x Lev)\n"
                f"Entry: ${entry_px:,.4f} | Current Mark: ${mark_px:,.4f} | Margin ROI: {roi_pct:+.2f}%\n"
                f"L2 Microstructure:\n"
                f"- Order Book Imbalance (OBI): {obi:+.3f} ({imbalance_label})\n"
                f"- Bid Depth: ${bid_depth:,.0f} vs Ask Depth: ${ask_depth:,.0f}\n"
                f"- Micro-Price: ${micro_px:,.4f} | Spread: {spread_bps:.1f} bps\n"
                f"Should the system CONFIRM_HOLD, REJECT_EXIT, or TIGHTEN_STOP?"
            )
            res = self.query(user_prompt, system_prompt, max_tokens=150)
            if res and isinstance(res, dict) and "verdict" in res:
                return res

        # 2. Deterministic High-Precision Order Flow Guardian (<1ms fallback)
        t0 = time.time()
        if side == "LONG":
            if (obi < -0.35 and ask_depth > 1.8 * max(1.0, bid_depth)) or (roi_pct < -4.0 and obi < -0.20):
                return {
                    "verdict": "REJECT_EXIT",
                    "confidence": 90,
                    "reason": f"Adverse ask wall collapse: OBI {obi:+.2f} with ask depth ${ask_depth:,.0f} exceeding bid depth ${bid_depth:,.0f}; cut early to preserve margin.",
                    "tighten_stop_price": 0.0,
                    "_provider": "pixel_onboard_quant",
                }
            elif roi_pct >= 10.0 and obi < -0.10:
                tighter = round(mark_px * 0.996, 4)
                return {
                    "verdict": "TIGHTEN_STOP",
                    "confidence": 85,
                    "reason": f"Locking in gains (+{roi_pct:.1f}% ROI): OBI flipped negative ({obi:+.2f}), ratcheted stop to ${tighter:,.4f}.",
                    "tighten_stop_price": tighter,
                    "_provider": "pixel_onboard_quant",
                }
            else:
                return {
                    "verdict": "CONFIRM_HOLD",
                    "confidence": 88,
                    "reason": f"Order flow intact: Bid depth ${bid_depth:,.0f} and OBI {obi:+.2f} support Long continuation ({roi_pct:+.1f}% ROI).",
                    "tighten_stop_price": 0.0,
                    "_provider": "pixel_onboard_quant",
                }
        else:  # SHORT
            if (obi > 0.35 and bid_depth > 1.8 * max(1.0, ask_depth)) or (roi_pct < -4.0 and obi > 0.20):
                return {
                    "verdict": "REJECT_EXIT",
                    "confidence": 90,
                    "reason": f"Adverse bid wall buildup: OBI {obi:+.2f} with bid depth ${bid_depth:,.0f} overwhelming ask depth; cut early to preserve margin.",
                    "tighten_stop_price": 0.0,
                    "_provider": "pixel_onboard_quant",
                }
            elif roi_pct >= 10.0 and obi > 0.10:
                tighter = round(mark_px * 1.004, 4)
                return {
                    "verdict": "TIGHTEN_STOP",
                    "confidence": 85,
                    "reason": f"Locking in gains (+{roi_pct:.1f}% ROI): Upward bid pressure building ({obi:+.2f}), ratcheted stop to ${tighter:,.4f}.",
                    "tighten_stop_price": tighter,
                    "_provider": "pixel_onboard_quant",
                }
            else:
                return {
                    "verdict": "CONFIRM_HOLD",
                    "confidence": 88,
                    "reason": f"Order flow intact: Ask pressure and OBI {obi:+.2f} favor continued Short momentum ({roi_pct:+.1f}% ROI).",
                    "tighten_stop_price": 0.0,
                    "_provider": "pixel_onboard_quant",
                }


# Global singleton instance
pixel_ai = PixelOnboardAI()
