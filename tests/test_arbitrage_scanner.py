"""
Unit tests for Delta-Neutral Funding Carry Arbitrage Scanner and MCP Integration.
Tests:
1. Microstructure Liquidity & Spread Pre-Filtering (spread <= 0.10%, top-5 depth >= $10k).
2. Elimination of high-slippage pairs.
3. Delta-Neutral Funding Carry Arbitrage Detection (< -50% APR and > +100% APR).
4. Combined Net Carry APR and Capital-Weighted APR calculations.
5. Atomic bridge serialization to bridge/funding_arbitrage.json.
6. MCP Tool methods in HyperliquidInfoClient.
"""

import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch

# Ensure repository root is on Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.scanner.arbitrage_scanner import ArbitrageScanner
from src.scanner.mcp_client import HyperliquidInfoClient


# =====================================================================
# 1. Microstructure Liquidity & Spread Pre-Filter Tests
# =====================================================================

def test_liquidity_and_spread_prefilter_pass():
    """Verify that a market with spread <= 0.10% and top-5 depth >= $10k passes."""
    scanner = ArbitrageScanner()
    
    # 0.05% spread, $50k depth
    mock_l2 = {
        "bids": [
            {"px": "100.00", "sz": "100.0"},  # $10,000
            {"px": "99.98", "sz": "100.0"},
            {"px": "99.96", "sz": "100.0"},
            {"px": "99.94", "sz": "100.0"},
            {"px": "99.92", "sz": "100.0"},
        ],
        "asks": [
            {"px": "100.05", "sz": "100.0"},  # $10,005
            {"px": "100.07", "sz": "100.0"},
            {"px": "100.09", "sz": "100.0"},
            {"px": "100.11", "sz": "100.0"},
            {"px": "100.13", "sz": "100.0"},
        ],
    }

    result = scanner.evaluate_liquidity_and_spread(
        coin="TEST_PASS",
        l2_data=mock_l2,
        max_spread_pct=0.10,
        min_top5_depth_usd=10000.0,
    )

    assert result["passed"] is True
    assert result["coin"] == "TEST_PASS"
    assert result["spread_pct"] == pytest.approx(0.05, rel=1e-2)
    assert result["top5_depth"] >= 10000.0
    assert "Passed" in result["reason"]


def test_liquidity_and_spread_prefilter_high_spread():
    """Verify that a market with bid-ask spread > 0.10% is filtered out."""
    scanner = ArbitrageScanner()

    # 0.25% spread (100.25 vs 100.00)
    mock_l2 = {
        "bids": [{"px": "100.00", "sz": "200.0"}],  # $20k
        "asks": [{"px": "100.25", "sz": "200.0"}],  # $20k
    }

    result = scanner.evaluate_liquidity_and_spread(
        coin="TEST_WIDE_SPREAD",
        l2_data=mock_l2,
        max_spread_pct=0.10,
        min_top5_depth_usd=10000.0,
    )

    assert result["passed"] is False
    assert result["spread_pct"] > 0.10
    assert "Spread" in result["reason"]


def test_liquidity_and_spread_prefilter_low_depth():
    """Verify that a market with top-5 depth < $10,000 is filtered out."""
    scanner = ArbitrageScanner()

    # Tight spread (0.02%), but only $2,000 top-5 depth
    mock_l2 = {
        "bids": [{"px": "100.00", "sz": "20.0"}],  # $2,000
        "asks": [{"px": "100.02", "sz": "20.0"}],  # $2,000
    }

    result = scanner.evaluate_liquidity_and_spread(
        coin="TEST_THIN_BOOK",
        l2_data=mock_l2,
        max_spread_pct=0.10,
        min_top5_depth_usd=10000.0,
    )

    assert result["passed"] is False
    assert result["top5_depth"] < 10000.0
    assert "Top-5 depth" in result["reason"]


def test_liquidity_and_spread_empty_orderbook():
    """Verify that an empty orderbook fails gracefully."""
    scanner = ArbitrageScanner()
    mock_l2 = {"bids": [], "asks": []}

    result = scanner.evaluate_liquidity_and_spread(coin="EMPTY", l2_data=mock_l2)
    assert result["passed"] is False
    assert "Insufficient order book depth" in result["reason"]


# =====================================================================
# 2. Delta-Neutral Funding Carry Arbitrage Detection Tests
# =====================================================================

def test_delta_neutral_funding_carry_detection():
    """
    Verify detection of pairs where one coin has extreme negative funding (< -50% APR)
    and another has extreme positive funding (> +100% APR), computing net carry APR.
    """
    scanner = ArbitrageScanner()

    # Mock market universe
    market_universe = [
        # Coin A: -60% APR (< -50% threshold) -> LONG Leg
        {"coin": "COIN_NEG", "funding_apr": -0.60, "oracle_price": 50.0, "volume_24h": 5000000.0},
        # Coin B: +140% APR (> +100% threshold) -> SHORT Leg
        {"coin": "COIN_POS", "funding_apr": 1.40, "oracle_price": 200.0, "volume_24h": 10000000.0},
        # Coin C: Normal funding (+10% APR) -> Should not qualify
        {"coin": "COIN_NORM", "funding_apr": 0.10, "oracle_price": 10.0, "volume_24h": 2000000.0},
    ]

    # Pre-computed liquid L2 books for both
    l2_cache = {
        "COIN_NEG": {
            "bids": [{"px": "50.00", "sz": "500.0"}],  # $25k depth
            "asks": [{"px": "50.02", "sz": "500.0"}],  # 0.04% spread
        },
        "COIN_POS": {
            "bids": [{"px": "200.00", "sz": "100.0"}],  # $20k depth
            "asks": [{"px": "200.10", "sz": "100.0"}],  # 0.05% spread
        },
    }

    pairs = scanner.scan_funding_arbitrage(
        max_negative_apr=-0.50,
        min_positive_apr=1.00,
        check_liquidity=True,
        market_universe=market_universe,
        l2_cache=l2_cache,
    )

    assert len(pairs) == 1
    pair = pairs[0]

    # Check pair details
    assert pair["pair_id"] == "COIN_NEG_LONG__COIN_POS_SHORT"
    assert pair["delta_neutral"] is True
    assert pair["strategy"] == "DELTA_NEUTRAL_FUNDING_CARRY"

    # Net carry APR = 1.40 - (-0.60) = 2.00 (+200% APR)
    assert pair["net_carry_apr"] == pytest.approx(2.00, rel=1e-3)
    assert pair["net_carry_apr_pct"] == pytest.approx(200.0, rel=1e-3)

    # Capital-weighted APR (50/50 allocation) = (| -0.60 | + 1.40) / 2 = 1.00 (100% APR)
    assert pair["capital_weighted_apr_pct"] == pytest.approx(100.0, rel=1e-3)

    # Legs
    assert pair["long_leg"]["coin"] == "COIN_NEG"
    assert pair["long_leg"]["action"] == "LONG"
    assert pair["long_leg"]["funding_apr"] == -0.60

    assert pair["short_leg"]["coin"] == "COIN_POS"
    assert pair["short_leg"]["action"] == "SHORT"
    assert pair["short_leg"]["funding_apr"] == 1.40


def test_delta_neutral_filters_high_slippage_pairs():
    """Verify that an extreme funding candidate with wide spread or low depth is rejected."""
    scanner = ArbitrageScanner()

    market_universe = [
        {"coin": "COIN_NEG", "funding_apr": -0.80, "oracle_price": 10.0, "volume_24h": 1000000.0},
        {"coin": "COIN_POS_ILLIQUID", "funding_apr": 1.50, "oracle_price": 5.0, "volume_24h": 1000000.0},
    ]

    # COIN_POS_ILLIQUID has wide spread (0.50% > 0.10%) and tiny depth ($1,000 < $10,000)
    l2_cache = {
        "COIN_NEG": {
            "bids": [{"px": "10.00", "sz": "2000.0"}],  # $20k
            "asks": [{"px": "10.005", "sz": "2000.0"}], # 0.05%
        },
        "COIN_POS_ILLIQUID": {
            "bids": [{"px": "5.00", "sz": "200.0"}],   # $1k
            "asks": [{"px": "5.025", "sz": "200.0"}],  # 0.50%
        },
    }

    # With liquidity filtering active -> 0 pairs qualify
    qualified_pairs = scanner.scan_funding_arbitrage(
        max_negative_apr=-0.50,
        min_positive_apr=1.00,
        check_liquidity=True,
        market_universe=market_universe,
        l2_cache=l2_cache,
    )
    assert len(qualified_pairs) == 0

    # With liquidity filtering disabled -> pair is detected
    unfiltered_pairs = scanner.scan_funding_arbitrage(
        max_negative_apr=-0.50,
        min_positive_apr=1.00,
        check_liquidity=False,
        market_universe=market_universe,
        l2_cache=l2_cache,
    )
    assert len(unfiltered_pairs) == 1
    assert unfiltered_pairs[0]["pair_id"] == "COIN_NEG_LONG__COIN_POS_ILLIQUID_SHORT"


# =====================================================================
# 3. Persistence & Serialization Tests
# =====================================================================

def test_save_funding_arbitrage_json(tmp_path):
    """Verify atomic persistence into funding_arbitrage.json with correct schema."""
    target_json = os.path.join(tmp_path, "funding_arbitrage.json")
    scanner = ArbitrageScanner(bridge_path=target_json)

    mock_pairs = [
        {
            "pair_id": "AAA_LONG__BBB_SHORT",
            "strategy": "DELTA_NEUTRAL_FUNDING_CARRY",
            "delta_neutral": True,
            "net_carry_apr": 1.75,
            "net_carry_apr_pct": 175.0,
            "capital_weighted_apr_pct": 87.5,
            "description": "Test Carry Pair",
            "long_leg": {"coin": "AAA", "funding_apr": -0.55, "oracle_price": 10.0},
            "short_leg": {"coin": "BBB", "funding_apr": 1.20, "oracle_price": 50.0},
            "timestamp": 1700000000.0,
        }
    ]

    saved_path = scanner.save_funding_arbitrage(mock_pairs, filepath=target_json)
    assert os.path.exists(saved_path)

    with open(saved_path, "r") as f:
        data = json.load(f)

    assert data["count"] == 1
    assert data["strategy"] == "DELTA_NEUTRAL_FUNDING_CARRY"
    assert "rpc_health" in data
    assert "thresholds" in data
    assert data["thresholds"]["max_negative_funding_apr"] == -0.50
    assert data["thresholds"]["min_positive_funding_apr"] == 1.00
    assert len(data["pairs"]) == 1
    assert data["pairs"][0]["pair_id"] == "AAA_LONG__BBB_SHORT"


# =====================================================================
# 4. MCP Client Integration Tests
# =====================================================================

def test_mcp_client_endpoints():
    """Verify MCP methods on HyperliquidInfoClient."""
    client = HyperliquidInfoClient()

    # 1. check_rpc_latency_and_rate_limits
    rpc_health = client.check_rpc_latency_and_rate_limits()
    assert rpc_health["status"] == "SUCCESS"
    assert "health_check" in rpc_health

    # 2. get_open_interest (BTC)
    oi_res = client.get_open_interest(coin="BTC")
    assert oi_res["status"] == "SUCCESS"
    assert "markets" in oi_res
    if oi_res["markets"]:
        m = oi_res["markets"][0]
        assert "symbol" in m
        assert "open_interest" in m

    # 3. get_funding_rates (BTC)
    fr_res = client.get_funding_rates(coin="BTC")
    assert fr_res["status"] == "SUCCESS"
    assert fr_res["coin"] == "BTC"
    assert "annualized_funding_apr" in fr_res

    # 4. get_high_throughput_l2 (BTC)
    l2_res = client.get_high_throughput_l2(coin="BTC")
    assert l2_res["status"] == "SUCCESS"
    assert len(l2_res.get("bids", [])) > 0
    assert len(l2_res.get("asks", [])) > 0
