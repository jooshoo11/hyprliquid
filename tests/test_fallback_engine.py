"""
Unit tests for Deterministic AI Fallback Engine (tests/test_fallback_engine.py).
"""

import os
import pytest
from unittest.mock import MagicMock
from src.risk.fallback_engine import AIFallbackEngine


def test_fallback_engine_initial_state(tmp_path):
    engine = AIFallbackEngine()
    summary = engine.get_status_summary()
    assert summary["status"] == "HEALTHY"
    assert summary["is_fallback_active"] is False
    assert summary["last_error"] is None


def test_fallback_trigger_and_recovery(tmp_path):
    engine = AIFallbackEngine(fallback_cooldown_seconds=1.0)
    
    # Trigger fallback due to simulated API 429 rate limit
    engine.trigger_fallback("AI Rate Limit Exceeded (HTTP 429)")
    summary = engine.get_status_summary()
    assert summary["status"] == "FALLBACK_ACTIVE"
    assert summary["is_fallback_active"] is True
    assert "429" in summary["last_error"]
    assert summary["fallback_count"] == 1

    # Recovery
    engine.recover()
    summary = engine.get_status_summary()
    assert summary["status"] == "HEALTHY"
    assert summary["is_fallback_active"] is False
    assert summary["last_error"] is None


def test_deterministic_prospects_generation():
    engine = AIFallbackEngine()
    mock_client = MagicMock()
    mock_client.get_meta_and_asset_ctxs.return_value = (
        {"universe": [{"name": "BTC"}, {"name": "ETH"}, {"name": "TAO"}]},
        [
            {"oraclePx": "90000", "prevDayPx": "85000", "funding": "0.00001", "dayNtlVlm": "500000000"},
            {"oraclePx": "3000", "prevDayPx": "2900", "funding": "0.00001", "dayNtlVlm": "200000000"},
            {"oraclePx": "300", "prevDayPx": "280", "funding": "0.00015", "dayNtlVlm": "50000000"},
        ]
    )

    prospects = engine.generate_deterministic_prospects(mock_client)
    assert isinstance(prospects, dict)
    assert len(prospects) > 0
    # Verify deterministic tag
    for coin, p in prospects.items():
        assert "[FALLBACK: RULE-BASED]" in p["reason"]
