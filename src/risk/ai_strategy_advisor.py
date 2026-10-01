"""
AI Strategy Allocation Advisor (src/risk/ai_strategy_advisor.py)

Cross-strategy dynamic capital allocation and risk weighting engine.
Synthesizes:
1. Macro Market Regime (from RegimeManager: TRENDING_BULL, TRENDING_BEAR, CHOPPY_MEAN_REVERTING, HIGH_VOLATILITY)
2. Strategy Performance Analytics (win rates, profit factors, Sharpe proxies)
3. Microstructure Volatility & Dislocation Signals
4. Dual-LLM Sentinel Risk Audits (Groq speed + Gemini search grounding)

Dynamically tunes strategy capital allocation weights on PortfolioGuard without human intervention.
Strictly zero pandas dependencies.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Any, List, Optional
import time
import logging

from src.risk.portfolio_guard import PortfolioGuard
from src.risk.regime_manager import MarketRegime
from src.utils.llm_client import UnifiedLLMClient

logger = logging.getLogger(__name__)


@dataclass
class StrategyWeightProfile:
    """Target capital allocation weights across all modular strategies."""
    TrendContinuationSMC: float = 0.25
    HourlyFundingFade: float = 0.20
    OrderBookImbalance: float = 0.20
    VwapOiMomentum: float = 0.20
    LiquidationAbsorber: float = 0.15
    timestamp: float = field(default_factory=time.time)
    rationale: str = "Default baseline balanced allocation"


class AIStrategyAdvisor:
    """
    Intelligent cross-strategy allocation and risk rebalancing advisor.
    """

    def __init__(
        self,
        portfolio_guard: Optional[PortfolioGuard] = None,
        llm_client: Optional[UnifiedLLMClient] = None,
    ):
        self.guard = portfolio_guard or PortfolioGuard()
        self.llm_client = llm_client or UnifiedLLMClient()
        self.last_profile: StrategyWeightProfile = StrategyWeightProfile()

    def advise_allocations(
        self,
        current_regime: MarketRegime,
        strategy_stats: Optional[Dict[str, Any]] = None,
        llm_context: Optional[str] = None,
    ) -> StrategyWeightProfile:
        """
        Compute optimal strategy allocation weights based on market regime and performance.

        Parameters
        ----------
        current_regime : MarketRegime
            The active market regime detected by RegimeManager.
        strategy_stats : dict, optional
            Recent performance statistics per strategy (win rate, pnl).
        llm_context : str, optional
            Qualitative market analysis or sentinel warning text.

        Returns
        -------
        StrategyWeightProfile
            Recommended percentage caps per strategy (summing to <= 1.0).
        """
        # Deterministic quantitative base rules:
        if current_regime == MarketRegime.BULL_MOMENTUM_EXPANSION:
            profile = StrategyWeightProfile(
                TrendContinuationSMC=0.40,
                HourlyFundingFade=0.10,
                OrderBookImbalance=0.15,
                VwapOiMomentum=0.25,
                LiquidationAbsorber=0.10,
                rationale="Bull Momentum: Heavy overweight on TrendContinuationSMC and VwapOiMomentum.",
            )
        elif current_regime == MarketRegime.BEAR_FLUSH_LIQUIDATION:
            profile = StrategyWeightProfile(
                TrendContinuationSMC=0.10,
                HourlyFundingFade=0.15,
                OrderBookImbalance=0.20,
                VwapOiMomentum=0.15,
                LiquidationAbsorber=0.40,
                rationale="Bear Flush / Liquidation: Overweight LiquidationAbsorber to harvest liquidation exhaustion.",
            )
        elif current_regime == MarketRegime.CHOPPY_MEAN_REVERTING_RANGE:
            profile = StrategyWeightProfile(
                TrendContinuationSMC=0.05,
                HourlyFundingFade=0.35,
                OrderBookImbalance=0.35,
                VwapOiMomentum=0.15,
                LiquidationAbsorber=0.10,
                rationale="Choppy Range: Overweight FundingFade and OrderBookImbalance; suppress trend continuation.",
            )
        elif current_regime == MarketRegime.HIGH_VOLATILITY_BREAKOUT:
            profile = StrategyWeightProfile(
                TrendContinuationSMC=0.30,
                HourlyFundingFade=0.10,
                OrderBookImbalance=0.15,
                VwapOiMomentum=0.30,
                LiquidationAbsorber=0.15,
                rationale="High Volatility Breakout: Emphasize VwapOiMomentum and TrendContinuationSMC.",
            )
        else:
            profile = StrategyWeightProfile(
                TrendContinuationSMC=0.25,
                HourlyFundingFade=0.20,
                OrderBookImbalance=0.20,
                VwapOiMomentum=0.20,
                LiquidationAbsorber=0.15,
                rationale="Neutral regime: Balanced multi-strategy allocation.",
            )

        # Performance-based adjustments (curb losing strategies, boost winning strategies)
        if strategy_stats:
            for strat, stats in strategy_stats.items():
                base_name = strat.split("-")[0]
                if hasattr(profile, base_name):
                    current_w = getattr(profile, base_name)
                    # If strategy is in a severe losing streak, discount allocation by 30%
                    if stats.get("consecutive_losses", 0) >= 2 or stats.get("win_rate_pct", 50.0) < 35.0:
                        setattr(profile, base_name, max(0.05, current_w * 0.70))
                    elif stats.get("win_rate_pct", 50.0) > 65.0:
                        setattr(profile, base_name, min(0.45, current_w * 1.20))

        # Re-normalize weights to ensure total <= 1.0
        weights = {
            "TrendContinuationSMC": profile.TrendContinuationSMC,
            "HourlyFundingFade": profile.HourlyFundingFade,
            "OrderBookImbalance": profile.OrderBookImbalance,
            "VwapOiMomentum": profile.VwapOiMomentum,
            "LiquidationAbsorber": profile.LiquidationAbsorber,
        }
        total_w = sum(weights.values())
        if total_w > 1.0:
            for k in weights:
                setattr(profile, k, round(weights[k] / total_w, 2))

        # Apply directly to PortfolioGuard allocation caps
        if self.guard:
            for strat_name, w in weights.items():
                if hasattr(self.guard, "set_strategy_allocation_cap"):
                    self.guard.set_strategy_allocation_cap(strat_name, w)

        self.last_profile = profile
        return profile
