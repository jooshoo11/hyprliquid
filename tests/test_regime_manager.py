"""
Unit Tests for Market Regime Manager (src/risk/regime_manager.py)
"""

import pytest
import polars as pl
from src.risk.regime_manager import MarketRegimeManager, MarketRegimeInfo
from src.risk.portfolio_guard import PortfolioGuard


@pytest.fixture
def regime_manager():
    return MarketRegimeManager()


@pytest.fixture
def portfolio_guard():
    return PortfolioGuard()


def test_bull_momentum_expansion(regime_manager, portfolio_guard):
    """Test Bull Momentum Expansion regime when breadth is high (>65%) and change > 1.2%."""
    records = []
    for i in range(50):
        # 40 green (80% breadth), average +3.0%
        records.append({
            "coin": f"COIN_{i}",
            "price": 100.0,
            "funding_apr": 12.0,
            "vol_24h": 10_000_000.0,
            "change_24h": 3.0 if i < 40 else -0.5,
        })
    df = pl.DataFrame(records)
    info = regime_manager.evaluate_universe(df)
    assert info.regime == "BULL_MOMENTUM_EXPANSION"
    assert info.breadth_pct == 80.0
    assert info.avg_change_24h > 1.2

    applied = regime_manager.apply_regime_to_engine(info, portfolio_guard)
    assert len(applied) > 0
    # In Bull Momentum, TrendContinuationSMC max positions should expand to 4, allocation to 2.5
    assert portfolio_guard.strategy_max_positions.get("TrendContinuationSMC") == 4
    assert portfolio_guard.get_strategy_allocation_cap("TrendContinuationSMC") == 2.5


def test_bear_market_flush(regime_manager, portfolio_guard):
    """Test Bear Market Flush regime when breadth is low (<=35%) and change < -1.2%."""
    records = []
    for i in range(50):
        # 10 green (20% breadth), average -3.5%
        records.append({
            "coin": f"COIN_{i}",
            "price": 50.0,
            "funding_apr": -2.0,
            "vol_24h": 8_000_000.0,
            "change_24h": -4.0 if i < 40 else 1.0,
        })
    df = pl.DataFrame(records)
    info = regime_manager.evaluate_universe(df)
    assert info.regime == "BEAR_MARKET_FLUSH"
    assert info.breadth_pct == 20.0
    assert info.avg_change_24h < -1.2

    applied = regime_manager.apply_regime_to_engine(info, portfolio_guard)
    assert len(applied) > 0
    # In Bear Flush, TrendContinuationSMC is throttled to 2 positions
    assert portfolio_guard.strategy_max_positions.get("TrendContinuationSMC") == 2


def test_overheated_crowded_longs(regime_manager, portfolio_guard):
    """Test Overheated Crowded Longs regime when >=5 coins have >+45% APR or avg funding > 40%."""
    records = []
    for i in range(50):
        # 6 coins have extreme funding APR > 60%
        records.append({
            "coin": f"COIN_{i}",
            "price": 20.0,
            "funding_apr": 70.0 if i < 6 else 15.0,
            "vol_24h": 5_000_000.0,
            "change_24h": 0.5,
        })
    df = pl.DataFrame(records)
    info = regime_manager.evaluate_universe(df)
    assert info.regime == "OVERHEATED_CROWDED_LONGS"
    assert info.high_funding_count >= 5

    applied = regime_manager.apply_regime_to_engine(info, portfolio_guard)
    assert len(applied) > 0
    # In Overheated Longs, HourlyFundingFade is prioritized to 4 positions with 2.5 cap
    assert portfolio_guard.strategy_max_positions.get("HourlyFundingFade") == 4
    assert portfolio_guard.get_strategy_allocation_cap("HourlyFundingFade") == 2.5


def test_negative_funding_short_squeeze(regime_manager, portfolio_guard):
    """Test Negative Funding Short Squeeze when >=3 coins have < -10% APR."""
    records = []
    for i in range(50):
        # 4 coins have < -15% APR
        records.append({
            "coin": f"COIN_{i}",
            "price": 10.0,
            "funding_apr": -20.0 if i < 4 else 8.0,
            "vol_24h": 6_000_000.0,
            "change_24h": 1.0,
        })
    df = pl.DataFrame(records)
    info = regime_manager.evaluate_universe(df)
    assert info.regime == "NEGATIVE_FUNDING_SHORT_SQUEEZE"
    assert info.negative_funding_count >= 3

    applied = regime_manager.apply_regime_to_engine(info, portfolio_guard)
    assert len(applied) > 0
    assert portfolio_guard.strategy_max_positions.get("VwapOiMomentum") == 3
    assert portfolio_guard.get_strategy_allocation_cap("VwapOiMomentum") == 2.5


def test_choppy_mean_reverting_range(regime_manager, portfolio_guard):
    """Test Choppy Mean-Reverting Range when breadth is balanced (45%) and flat change."""
    records = []
    for i in range(50):
        records.append({
            "coin": f"COIN_{i}",
            "price": 30.0,
            "funding_apr": 10.0,
            "vol_24h": 4_000_000.0,
            "change_24h": 0.2 if i < 25 else -0.3,
        })
    df = pl.DataFrame(records)
    info = regime_manager.evaluate_universe(df)
    assert info.regime == "CHOPPY_MEAN_REVERTING_RANGE"
    assert 35.0 <= info.breadth_pct <= 65.0

    applied = regime_manager.apply_regime_to_engine(info, portfolio_guard)
    assert len(applied) > 0
    # In range mode, OrderBookImbalance scalper is allowed up to 2 positions
    assert portfolio_guard.strategy_max_positions.get("OrderBookImbalance") == 2


def test_dynamic_strategy_attribute_tweaks(regime_manager, portfolio_guard):
    """Test dynamic attribute injection into strategy instances across regimes."""
    class MockStrategy:
        pass

    continuation = MockStrategy()
    funding = MockStrategy()
    vwap = MockStrategy()
    scalp = MockStrategy()

    # 1. Bull Momentum
    info_bull = MarketRegimeInfo(
        regime="BULL_MOMENTUM_EXPANSION",
        breadth_pct=75.0,
        avg_change_24h=2.5,
        avg_funding_apr=15.0,
        volatility_score=1.2,
        high_funding_count=1,
        negative_funding_count=0,
        sentiment="Bullish",
        active_tweaks=[],
    )
    regime_manager.apply_regime_to_engine(
        info_bull, portfolio_guard, continuation, funding, scalp, vwap
    )
    assert getattr(continuation, "dynamic_rr_ratio") == 3.0
    assert getattr(funding, "dynamic_min_apr") == 80.0
    assert getattr(vwap, "dynamic_oi_zscore_threshold") == 0.8
    assert getattr(scalp, "dynamic_skew_threshold") == 4.0

    # 2. Choppy Range
    info_chop = MarketRegimeInfo(
        regime="CHOPPY_MEAN_REVERTING_RANGE",
        breadth_pct=50.0,
        avg_change_24h=0.1,
        avg_funding_apr=10.0,
        volatility_score=0.5,
        high_funding_count=0,
        negative_funding_count=0,
        sentiment="Choppy",
        active_tweaks=[],
    )
    regime_manager.apply_regime_to_engine(
        info_chop, portfolio_guard, continuation, funding, scalp, vwap
    )
    assert getattr(continuation, "dynamic_rr_ratio") == 1.8
    assert getattr(funding, "dynamic_min_apr") == 50.0
    assert getattr(vwap, "dynamic_oi_zscore_threshold") == 1.8
    assert getattr(scalp, "dynamic_skew_threshold") == 3.0
    assert getattr(scalp, "dynamic_take_profit_ticks") == 18


def test_empty_dataframe_baseline(regime_manager):
    """Test safe fallback when dataframe is empty."""
    df = pl.DataFrame()
    info = regime_manager.evaluate_universe(df)
    assert info.regime == "CHOPPY_MEAN_REVERTING_RANGE"
    assert info.breadth_pct == 50.0
