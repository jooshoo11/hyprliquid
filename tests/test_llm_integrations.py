"""
Unit & Integration Tests for Groq & Google AI Studio LLM modules (tests/test_llm_integrations.py).
"""

import os
import json
import time
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from src.utils.llm_client import UnifiedLLMClient, clean_json_text
from src.monitoring.groq_sentinel import GroqRiskSentinel
from src.scanner.ai_studio_prospector import AIStudioProspector


def test_clean_json_text():
    raw_markdown = "```json\n{\"status\": \"OK\", \"value\": 42}\n```"
    cleaned = clean_json_text(raw_markdown)
    assert json.loads(cleaned) == {"status": "OK", "value": 42}

    plain = "{\"status\": \"OK\"}"
    assert clean_json_text(plain) == plain


def test_llm_client_status():
    client = UnifiedLLMClient(groq_api_key="", gemini_api_key="")
    status = client.get_status()
    assert "groq" in status
    assert "gemini" in status
    assert status["groq"]["ready"] is False
    assert status["gemini"]["ready"] is False


def test_llm_client_groq_mock():
    client = UnifiedLLMClient(groq_api_key="mock_key")
    mock_groq = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"action": "CANCEL_ORPHANS", "reason": "Orphan detected"})
    mock_groq.chat.completions.create.return_value.choices = [mock_choice]
    client.groq_client = mock_groq

    res = client.query_groq_json("prompt", "sys")
    assert res is not None
    assert res["action"] == "CANCEL_ORPHANS"
    assert res["_provider"] == "groq"


def test_llm_client_gemini_mock():
    client = UnifiedLLMClient(gemini_api_key="mock_key")
    mock_genai = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json.dumps([{"coin": "ENA", "validated": True, "news_sentiment": "BULLISH"}])
    mock_genai.models.generate_content.return_value = mock_resp
    client.genai_client = mock_genai

    res = client.query_gemini_json("prompt", "sys")
    assert res is not None
    assert len(res) == 1
    assert res[0]["coin"] == "ENA"


def test_groq_sentinel_orphan_order_detection(tmp_path):
    cmd_file = str(tmp_path / "ai_commands.json")
    hb_file = str(tmp_path / "groq_sentinel.json")

    sentinel = GroqRiskSentinel(commands_path=cmd_file, heartbeat_path=hb_file)

    # Mock engine state having an orphaned order on APEX while active position is only ENA
    mock_state = {
        "equity": 100.0,
        "cash_balance": 95.0,
        "net_unrealized": 5.0,
        "positions": [
            {"coin": "ENA", "side": "LONG", "roi_pct": 1.2, "unrealized_pnl": 1.5, "strategy": "SMC Trend"}
        ],
        "open_orders": [
            {"order_id": "O-1", "instrument_id": "APEX-USD-PERP.HYPERLIQUID", "side": "SELL", "type": "MARKET"}
        ]
    }
    sentinel.fetch_engine_state = MagicMock(return_value=mock_state)

    audit = sentinel.audit_once(dry_run=False)
    assert audit["action"] == "CANCEL_ORPHANS"
    assert "APEX" in audit["target_coins"]

    # Verify that command was dispatched to ai_commands.json
    assert os.path.exists(cmd_file)
    with open(cmd_file, "r") as f:
        cmds = json.load(f)
    assert len(cmds) == 1
    assert cmds[0]["action"] == "CANCEL_ORPHANS"
    assert "APEX" in cmds[0]["target_coins"]


def test_ai_studio_prospector_passthrough_when_offline():
    prospector = AIStudioProspector()
    prospector.is_available = MagicMock(return_value=False)

    raw_candidates = [
        {"coin": "ENA", "bias": "LONG", "conviction_score": 88},
        {"coin": "WLD", "bias": "LONG", "conviction_score": 85},
    ]
    enhanced = prospector.enhance_prospects(raw_candidates)
    assert len(enhanced) == 2
    assert enhanced[0]["coin"] == "ENA"
    assert enhanced[0]["conviction_score"] == 88


def test_ai_studio_prospector_catalyst_grounding_mock():
    mock_llm = MagicMock()
    mock_llm.is_gemini_ready.return_value = True

    # Mock LLM returning a TOXIC catalyst for MEMECOIN and BULLISH for ENA
    mock_llm.query_gemini_json.return_value = [
        {
            "coin": "MEME",
            "validated": False,
            "news_sentiment": "TOXIC_CATALYST",
            "adjusted_conviction": 20,
            "catalyst_verdict": "Active bridge vulnerability under investigation.",
            "citation": "coindesk.com"
        },
        {
            "coin": "ENA",
            "validated": True,
            "news_sentiment": "BULLISH",
            "adjusted_conviction": 95,
            "catalyst_verdict": "Ecosystem TVL reached new all-time high.",
            "citation": "defillama.com"
        }
    ]

    prospector = AIStudioProspector(llm_client=mock_llm)
    raw = [
        {"coin": "MEME", "bias": "LONG", "conviction_score": 85, "rationale": "High volume"},
        {"coin": "ENA", "bias": "LONG", "conviction_score": 80, "rationale": "Trend setup"},
    ]

    enhanced = prospector.enhance_prospects(raw, use_search=True)
    # ENA should be promoted to #1 with boosted conviction, and MEME penalized
    assert enhanced[0]["coin"] == "ENA"
    assert enhanced[0]["conviction_score"] >= 95
    assert "🌟 [AI Studio Verified]" in enhanced[0]["rationale"]

    assert enhanced[1]["coin"] == "MEME"
    assert enhanced[1]["conviction_score"] <= 40
    assert "⚠️ [AI Studio VETO]" in enhanced[1]["rationale"]
