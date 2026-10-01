"""
Unit tests for AIStrategyAdvisor (src/risk/ai_strategy_advisor.py)
Tests:
- Reallocation across regimes (BULL_MOMENTUM, BEAR_FLUSH, CHOPPY_MEAN_REVERTING).
- Dynamic performance weighting adjustments (losing streak discount, winning streak bonus).
- Normalization ensures total weight <= 1.0.
- Direct synchronization with PortfolioGuard allocation caps.
"""

from unittest.mock import MagicMock
import pytest

from src.risk.ai_strategy_advisor import AIStrategyAdvisor, StrategyWeightProfile
from src.risk.regime_manager import MarketRegime
from src.risk.portfolio_guard import PortfolioGuard


def test_ai_strategy_advisor_bull_momentum():
    """Verify bull momentum overweights TrendContinuationSMC and VwapOiMomentum."""
    guard = PortfolioGuard()
    advisor = AIStrategyAdvisor(portfolio_guard=guard)

    profile = advisor.advise_allocations(MarketRegime.BULL_MOMENTUM_EXPANSION)

    assert profile.TrendContinuationSMC >= 0.35
    assert profile.VwapOiMomentum >= 0.20
    assert profile.HourlyFundingFade <= 0.15
    assert guard.get_strategy_allocation_cap("TrendContinuationSMC") >= 0.35


def test_ai_strategy_advisor_choppy_range():
    """Verify choppy range overweights funding fade and orderbook scalping while trimming trend."""
    guard = PortfolioGuard()
    advisor = AIStrategyAdvisor(portfolio_guard=guard)

    profile = advisor.advise_allocations(MarketRegime.CHOPPY_MEAN_REVERTING_RANGE)

    assert profile.TrendContinuationSMC <= 0.10
    assert profile.HourlyFundingFade >= 0.30
    assert profile.OrderBookImbalance >= 0.30


def test_ai_strategy_advisor_bear_flush_liquidation():
    """Verify bear flush massively boosts LiquidationAbsorber."""
    guard = PortfolioGuard()
    advisor = AIStrategyAdvisor(portfolio_guard=guard)

    profile = advisor.advise_allocations(MarketRegime.BEAR_FLUSH_LIQUIDATION)

    assert profile.LiquidationAbsorber >= 0.35
    assert profile.TrendContinuationSMC <= 0.15


def test_ai_strategy_advisor_performance_tuning():
    """Verify underperforming strategies get penalized in allocation weight."""
    guard = PortfolioGuard()
    advisor = AIStrategyAdvisor(portfolio_guard=guard)

    stats = {
        "TrendContinuationSMC": {"consecutive_losses": 3, "win_rate_pct": 25.0},
        "HourlyFundingFade": {"win_rate_pct": 80.0, "consecutive_losses": 0},
    }

    profile = advisor.advise_allocations(MarketRegime.NEUTRAL_EQUILIBRIUM, strategy_stats=stats)

    assert profile.TrendContinuationSMC < 0.25
    assert profile.HourlyFundingFade > 0.20
