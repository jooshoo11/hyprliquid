"""
Unit tests for open-source GitHub tool integrations (tests/test_github_integrations.py)
Tests:
- instructor + Pydantic schema validation (jxnl/instructor)
- Hummingbot microstructure math (hummingbot/hummingbot)
- Hyperliquid native agent wallet wrapper (hyperliquid-dex/hyperliquid-python-sdk)
- Chainstack HyperCore RPC health monitor (chainstacklabs/hyperliquid-trading-bot)
"""

import pytest
from src.utils.schemas import RiskAuditDecision, GroundedProspect, OrderBookMicrostructure
from src.scanner.microstructure import (
    calculate_micro_price,
    calculate_order_book_imbalance,
    calculate_spread_bps,
    estimate_market_impact,
    analyze_l2_snapshot,
)
from src.execution.agent_wallet import HyperliquidAgentWallet
from src.utils.rpc_health import RPCHealthMonitor


def test_instructor_pydantic_schemas():
    # Verify RiskAuditDecision schema
    decision = RiskAuditDecision(
        status="HEALTHY",
        action="NONE",
        target_coins=["NEAR"],
        reason="Account within risk limits",
        confidence=95.0,
    )
    assert decision.status == "HEALTHY"
    assert decision.action == "NONE"
    assert "NEAR" in decision.target_coins

    # Verify GroundedProspect schema
    prospect = GroundedProspect(
        coin="ENA",
        validated=True,
        news_sentiment="BULLISH",
        adjusted_conviction=98.0,
        catalyst_verdict="sUSDe supply expanding cleanly",
        citation="defillama.com",
    )
    assert prospect.coin == "ENA"
    assert prospect.news_sentiment == "BULLISH"


def test_hummingbot_micro_price():
    # Symmetrical volume -> micro price equals mid price
    mid = calculate_micro_price(best_bid=100.0, best_ask=102.0, bid_size=10.0, ask_size=10.0)
    assert mid == 101.0

    # Bid size >> Ask size (heavy buy pressure) -> micro price shifts upward toward ask price
    skewed = calculate_micro_price(best_bid=100.0, best_ask=102.0, bid_size=90.0, ask_size=10.0)
    assert skewed == 101.8


def test_hummingbot_order_book_imbalance():
    bids = [{"px": 100.0, "sz": 50.0}]
    asks = [{"px": 101.0, "sz": 50.0}]
    obi_balanced = calculate_order_book_imbalance(bids, asks, depth=1)
    # 5000 bid vs 5050 ask -> slightly negative
    assert -0.05 <= obi_balanced <= 0.05

    # Pure bid wall -> OBI near +1.0
    heavy_bids = [{"px": 100.0, "sz": 1000.0}]
    thin_asks = [{"px": 101.0, "sz": 1.0}]
    obi_buy = calculate_order_book_imbalance(heavy_bids, thin_asks, depth=1)
    assert obi_buy > 0.95


def test_hummingbot_spread_and_market_impact():
    spread_bps = calculate_spread_bps(best_bid=100.0, best_ask=100.10)
    assert 9.9 <= spread_bps <= 10.1

    levels = [
        {"px": 100.0, "sz": 5.0},   # $500 depth
        {"px": 101.0, "sz": 10.0},  # $1010 depth
    ]
    # Market order of $400 should be fully filled at level 1 ($100.0)
    fill_px, slippage = estimate_market_impact("BUY", 400.0, levels)
    assert fill_px == 100.0
    assert slippage == 0.0

    # Market order of $1000 will consume all $500 of level 1 and part of level 2
    fill_px_large, slip_large = estimate_market_impact("BUY", 1000.0, levels)
    assert fill_px_large > 100.0
    assert slip_large > 0.0


def test_analyze_l2_snapshot():
    mock_l2 = {
        "levels": [
            [{"px": 10.0, "sz": 1000.0}],  # Bids ($10,000 depth)
            [{"px": 10.005, "sz": 800.0}], # Asks ($8,004 depth)
        ]
    }
    analysis = analyze_l2_snapshot("TEST", mock_l2)
    assert analysis is not None
    assert analysis.coin == "TEST"
    assert analysis.spread_bps < 10.0
    assert analysis.is_liquid is True
    assert analysis.micro_price > 10.0


def test_agent_wallet_diagnostics():
    wallet = HyperliquidAgentWallet(
        main_address="0x0000000000000000000000000000000000000000",
        agent_key="0x0000000000000000000000000000000000000000000000000000000000000000",
        network="testnet",
    )
    status = wallet.get_status()
    assert status["sdk_installed"] is True
    assert status["mode"] == "PAPER_LOCAL_EMULATION"

    # Placing order in paper mode should succeed safely with mock confirmation
    res = wallet.place_order("NEAR", is_buy=True, size=10.0, limit_px=5.40)
    assert res is not None
    assert res["status"] == "MOCK_PAPER_FILLED"


def test_rpc_health_monitor():
    monitor = RPCHealthMonitor(private_rpc_url=None)
    health = monitor.get_health(force_refresh=True)
    assert "public_rpc" in health
    assert "active_endpoint" in health
    assert "effective_latency_ms" in health
