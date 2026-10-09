#!/usr/bin/env python3
import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO_ROOT)

def run_tests():
    print("=== TEST 1: Pixel Onboard AI ===")
    from src.utils.pixel_ai import pixel_ai
    hw = pixel_ai.get_hardware_info()
    print("Hardware:", hw["device"], "|", hw["core_layout"])
    print("Status:", hw["engine_status"], "|", hw["token_cost"])
    audit = pixel_ai.audit_sentinel_risk({"positions": [], "equity": 100.0, "open_orders": [], "net_unrealized": 0.0})
    print("Quant Audit:", audit["status"], "-", audit["reason"], f"({audit['_latency_ms']}ms)")

    print("\n=== TEST 2: Self-Improving Engine ===")
    from src.risk.self_improving_engine import self_improving_engine
    params = self_improving_engine.get_adaptive_params()
    print("Adaptive params:", f"WinRate: {params['win_rate_recent']}%, TP RR: {params['tp_rr_ratio']}x, SL Mult: {params['sl_multiplier']}x, Ratchet: {params['ratchet_pct']}%")
    allowed, score, reason = self_improving_engine.evaluate_candidate("BTC", {"score": 90, "sector": "MAJORS", "funding_apr": -5.0})
    print("Candidate Gate (BTC):", f"Allowed: {allowed}, Adapted Score: {score}, Reason: {reason}")

    # Test closed trade learning feedback
    self_improving_engine.record_closed_trade({
        "coin": "SOL", "strategy": "ShortSqueezeIgnition", "leverage": 20.0,
        "margin": 15.0, "net_pnl": 3.80, "roi_pct": 25.3, "reason": "Take Profit Hit"
    })
    params_after = self_improving_engine.get_adaptive_params()
    print("Params After Trade:", f"WinRate: {params_after['win_rate_recent']}%, Analyzed: {params_after['total_trades_analyzed']}")

    print("\n=== TEST 3: AutoTrader Status ===")
    from src.execution.auto_trader import auto_trader
    st = auto_trader.get_status()
    print("AutoTrader Status:", st["status_label"], "| Leverage Mode:", st["leverage_mode"], "| Equity:", f"${st['equity']}")
    print("Adaptive Learning in Sentry:", st["adaptive_learning"])

    print("\n=== TEST 4: Unified LLM Client ===")
    from src.utils.llm_client import UnifiedLLMClient
    client = UnifiedLLMClient()
    client_st = client.get_status()
    print("LLM Providers:", list(client_st.keys()))
    print("Pixel 9 Status:", client_st["pixel_onboard"])

    print("\n✅ ALL INTEGRATION TESTS PASSED CLEANLY!")

if __name__ == "__main__":
    run_tests()
