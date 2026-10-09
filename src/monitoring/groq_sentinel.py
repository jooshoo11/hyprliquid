"""
Groq-Powered Real-Time Risk Sentinel (src/monitoring/groq_sentinel.py)

Monitors the live Hyperliquid engine state every 15-30s using ultra-fast Groq LPU inference.
Detects:
1. Phantom/orphaned open orders on flat instruments.
2. Rapid drawdown spikes and correlated position adverse moves.
3. Extended losing streaks or severe negative ROI anomalies.

Outputs:
- bridge/groq_sentinel.json: Live diagnostic heartbeat for web dashboard
- bridge/ai_commands.json: Autonomous corrective directives (CANCEL_ORPHANS, CLOSE_POSITION, FLATTEN_ALL)
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import os
import json
import time
import argparse
import requests
from typing import Dict, Any, List, Optional

from src.utils.llm_client import UnifiedLLMClient

SYSTEM_PROMPT = """You are an elite quantitative risk sentinel auditing an autonomous Hyperliquid trading bot.
You receive a real-time JSON snapshot containing:
- "equity": current portfolio equity ($)
- "cash_balance": cash ($)
- "net_unrealized": floating P&L ($)
- "positions": list of open positions (coin, side, roi_pct, unrealized_pnl, strategy)
- "open_orders": list of active open orders in the exchange cache
- "realized_pnl": session net realized P&L

Your job:
1. Detect any "ORPHANED_ORDERS": open orders for coins that have NO matching active position.
2. Detect "CRITICAL_DRAWDOWN": any single position with ROI <= -2.4% or total unrealized loss exceeding 15% of equity.
3. Detect "HEALTHY": normal operation within risk tolerances.

Respond STRICTLY with a raw JSON object matching this schema:
{
  "status": "HEALTHY" | "CAUTION" | "ALERT" | "CRITICAL",
  "action": "NONE" | "CANCEL_ORPHANS" | "CLOSE_POSITION" | "FLATTEN_ALL",
  "target_coins": ["COIN1"],
  "reason": "1 concise sentence explaining your risk decision",
  "confidence": 0-100
}"""


class GroqRiskSentinel:
    """
    Continuous real-time risk monitor leveraging Groq LPU speed.
    """

    def __init__(
        self,
        engine_url: str = "http://localhost:8000/api/state",
        commands_path: Optional[str] = None,
        heartbeat_path: Optional[str] = None,
        llm_client: Optional[UnifiedLLMClient] = None,
    ):
        self.engine_url = engine_url
        self.commands_path = commands_path or os.path.join(REPO_ROOT, "bridge", "ai_commands.json")
        self.heartbeat_path = heartbeat_path or os.path.join(REPO_ROOT, "bridge", "groq_sentinel.json")
        self.llm_client = llm_client or UnifiedLLMClient()
        self.last_audit: Optional[Dict[str, Any]] = None

    def fetch_engine_state(self) -> Optional[Dict[str, Any]]:
        """Fetch live state from API server or fallback to local bridge files."""
        try:
            resp = requests.get(self.engine_url, timeout=2.5)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass

        # Fallback to bridge files if API server is not reached directly
        active_trades_path = os.path.join(REPO_ROOT, "bridge", "active_trades.json")
        paper_state_path = os.path.join(REPO_ROOT, "bridge", "paper_state.json")
        state: Dict[str, Any] = {}
        if os.path.exists(active_trades_path):
            try:
                with open(active_trades_path, "r") as f:
                    state.update(json.load(f))
            except Exception:
                pass
        if os.path.exists(paper_state_path):
            try:
                with open(paper_state_path, "r") as f:
                    state.update(json.load(f))
            except Exception:
                pass

        return state if state else None

    def audit_once(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Run a single risk audit cycle using Groq.
        """
        state = self.fetch_engine_state()
        if not state:
            return {"status": "ERROR", "reason": "Could not connect to engine state"}

        # Prepare compact payload to keep tokens low (~250 tokens)
        positions = state.get("positions") or state.get("open_positions") or []
        open_orders = state.get("open_orders") or []

        pos_summary = []
        active_coins = set()
        for p in positions:
            coin = p.get("coin", "").upper()
            active_coins.add(coin)
            pos_summary.append({
                "coin": coin,
                "side": p.get("side"),
                "roi_pct": round(float(p.get("roi_pct", 0.0)), 2),
                "unrealized_pnl": round(float(p.get("unrealized_pnl", 0.0)), 2),
                "strategy": p.get("strategy", "Unknown"),
            })

        order_summary = []
        for o in open_orders:
            instr = o.get("instrument_id", "")
            coin = instr.split("-")[0].upper()
            order_summary.append({
                "order_id": o.get("order_id"),
                "coin": coin,
                "side": o.get("side"),
                "type": o.get("type"),
                "is_orphan": coin not in active_coins,
            })

        compact_snapshot = {
            "equity": round(float(state.get("equity", 100.0)), 2),
            "cash_balance": round(float(state.get("cash_balance", 100.0)), 2),
            "net_unrealized": round(float(state.get("net_unrealized", 0.0)), 2),
            "realized_pnl": round(float(state.get("net_realized_pnl", 0.0)), 2),
            "positions": pos_summary,
            "open_orders": order_summary,
            "has_orphaned_orders": any(o["is_orphan"] for o in order_summary),
        }

        # Deterministic check for immediate action even if LLM is offline
        deterministic_action = "NONE"
        deterministic_targets = []
        deterministic_reason = "System nominal"

        if compact_snapshot["has_orphaned_orders"]:
            deterministic_action = "CANCEL_ORPHANS"
            deterministic_targets = [o["coin"] for o in order_summary if o["is_orphan"]]
            deterministic_reason = f"Detected {len(deterministic_targets)} orphaned orders on flat instruments."

        # Priority 1: Pixel 9 On-Device AI Engine (zero-token, offline local inference)
        decision = None
        if hasattr(self.llm_client, "audit_risk_locally"):
            try:
                local_decision = self.llm_client.audit_risk_locally(compact_snapshot)
                if local_decision and isinstance(local_decision, dict) and "action" in local_decision:
                    decision = local_decision
            except Exception:
                decision = None

        # Priority 2: If local not used and Groq is configured, query Groq LPU
        if not decision and self.llm_client.is_groq_ready():
            prompt = json.dumps(compact_snapshot)
            try:
                from src.utils.schemas import RiskAuditDecision
                pydantic_res = self.llm_client.query_groq_structured(
                    prompt=prompt,
                    response_model=RiskAuditDecision,
                    system_prompt=SYSTEM_PROMPT,
                    model="openai/gpt-oss-20b",
                )
                if pydantic_res:
                    decision = pydantic_res.model_dump()
                    decision["_provider"] = "groq_instructor"
            except Exception:
                decision = None

            if not decision:
                decision = self.llm_client.query_groq_json(
                    prompt=prompt,
                    system_prompt=SYSTEM_PROMPT,
                    model="openai/gpt-oss-20b",
                    temperature=0.1,
                    max_tokens=250,
                )

        if not decision or not isinstance(decision, dict) or "action" not in decision:
            # Fallback to deterministic audit result
            decision = {
                "status": "ALERT" if deterministic_action != "NONE" else "HEALTHY",
                "action": deterministic_action,
                "target_coins": deterministic_targets,
                "reason": deterministic_reason,
                "confidence": 95,
                "_provider": "deterministic_fallback",
            }

        decision["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        decision["active_positions_count"] = len(pos_summary)
        decision["open_orders_count"] = len(order_summary)
        self.last_audit = decision

        # Write live heartbeat for UI dashboards
        try:
            tmp_hb = f"{self.heartbeat_path}.tmp"
            with open(tmp_hb, "w") as f:
                json.dump(decision, f, indent=2)
            os.replace(tmp_hb, self.heartbeat_path)
        except Exception:
            pass

        # If action requires intervention and not dry_run, write to ai_commands.json
        if not dry_run and decision.get("action") in ("CANCEL_ORPHANS", "CLOSE_POSITION", "FLATTEN_ALL"):
            self._dispatch_command(decision)

        return decision

    def evaluate_fill(self, fill_event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sub-100ms Event-Driven Toxic Fill Audit.
        Evaluates execution fill for adverse selection, excessive slippage, or spread blowout.
        """
        t0 = time.time()
        coin = fill_event.get("coin", "").upper()
        fill_px = float(fill_event.get("fill_px", 0.0))
        slippage_bps = float(fill_event.get("slippage_bps", 0.0))
        spread_bps = float(fill_event.get("spread_bps", 0.0))

        is_toxic = slippage_bps > 25.0 or spread_bps > 40.0
        action = "CLOSE_POSITION" if is_toxic else "NONE"
        reason = (
            f"Toxic adverse selection detected on {coin} fill: {slippage_bps:.1f} bps slippage, {spread_bps:.1f} bps spread"
            if is_toxic else f"Nominal fill on {coin} ({slippage_bps:.1f} bps slippage)"
        )

        decision = {
            "status": "CRITICAL" if is_toxic else "HEALTHY",
            "action": action,
            "target_coins": [coin] if is_toxic else [],
            "reason": reason,
            "confidence": 95 if is_toxic else 90,
            "_latency_ms": round((time.time() - t0) * 1000, 2),
            "_provider": "event_driven_sentry",
        }

        if is_toxic:
            self._dispatch_command(decision)

        return decision


    def _dispatch_command(self, decision: Dict[str, Any]) -> None:
        """Append actionable commands into bridge/ai_commands.json for engine execution."""
        try:
            existing_cmds: List[Dict[str, Any]] = []
            if os.path.exists(self.commands_path):
                with open(self.commands_path, "r") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        existing_cmds = loaded
                    elif isinstance(loaded, dict) and loaded:
                        existing_cmds = [loaded]

            action = decision.get("action")
            targets = decision.get("target_coins") or []
            reason = decision.get("reason", "Groq Sentinel Autonomous Risk Control")

            new_cmd = {
                "source": "GROQ_SENTINEL",
                "action": action,
                "timestamp": time.time(),
                "reason": reason,
                "target_coins": targets,
            }

            # Avoid spamming duplicate identical command within 30 seconds
            is_dup = any(
                c.get("action") == action and (time.time() - c.get("timestamp", 0)) < 30.0
                for c in existing_cmds
            )
            if not is_dup:
                existing_cmds.append(new_cmd)
                tmp_cmd = f"{self.commands_path}.tmp"
                with open(tmp_cmd, "w") as f:
                    json.dump(existing_cmds, f, indent=2)
                os.replace(tmp_cmd, self.commands_path)
                print(f"⚡ [Groq Sentinel] Dispatched {action} command: {reason}")
        except Exception as e:
            print(f"Error dispatching Groq Sentinel command: {e}")

    def run_loop(self, interval_seconds: float = 20.0, dry_run: bool = False) -> None:
        """Run continuous audit loop."""
        print(f"🛡️ [Groq Sentinel] Starting real-time audit loop (interval={interval_seconds}s, dry_run={dry_run})...")
        while True:
            t0 = time.time()
            res = self.audit_once(dry_run=dry_run)
            status = res.get("status", "UNKNOWN")
            action = res.get("action", "NONE")
            reason = res.get("reason", "")
            lat = res.get("_latency_ms", 0.0)
            prov = res.get("_provider", "groq")
            print(f"[{time.strftime('%H:%M:%S')}] [{prov.upper()}] Status={status} | Action={action} | {reason} ({lat}ms)")
            elapsed = time.time() - t0
            time.sleep(max(1.0, interval_seconds - elapsed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Groq Real-Time Risk Sentinel for Hyperliquid")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=float, default=20.0, help="Poll interval in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Do not write actions to ai_commands.json")
    args = parser.parse_args()

    sentinel = GroqRiskSentinel()
    if args.once:
        result = sentinel.audit_once(dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
    else:
        sentinel.run_loop(interval_seconds=args.interval, dry_run=args.dry_run)
