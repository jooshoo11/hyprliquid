"""
Market Regime Manager (src/risk/regime_manager.py)
Autonomous market regime detection and dynamic multi-strategy parameter adaptation.

Every 15 minutes, during the Prospector scan across top 50 perpetuals, this engine:
1. Calculates cross-asset breadth (% assets green, mean/median 24h change).
2. Calculates aggregate funding carry APR and distribution skew.
3. Quantifies volatility dispersion.
4. Classifies the prevailing macro regime:
   - BULL_MOMENTUM_EXPANSION: Broad-based accumulation, healthy carry.
   - BEAR_MARKET_FLUSH: Broad-based liquidation, negative breadth.
   - OVERHEATED_CROWDED_LONGS: Extreme positive funding, cascade flush risk.
   - NEGATIVE_FUNDING_SHORT_SQUEEZE: Negative funding crowd, explosive upside short squeeze risk.
   - CHOPPY_MEAN_REVERTING_RANGE: Low directional follow-through, range-bound.
5. Dynamically tweaks active bot parameters, strategy position limits, and risk allocation caps.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
import polars as pl


@dataclass
class MarketRegimeInfo:
    regime: str
    breadth_pct: float
    avg_change_24h: float
    avg_funding_apr: float
    volatility_score: float
    high_funding_count: int
    negative_funding_count: int
    sentiment: str
    active_tweaks: List[str]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime,
            "breadth_pct": round(self.breadth_pct, 1),
            "avg_change_24h": round(self.avg_change_24h, 2),
            "avg_funding_apr": round(self.avg_funding_apr, 1),
            "volatility_score": round(self.volatility_score, 2),
            "high_funding_count": self.high_funding_count,
            "negative_funding_count": self.negative_funding_count,
            "sentiment": self.sentiment,
            "active_tweaks": self.active_tweaks,
            "timestamp": self.timestamp,
        }


class MarketRegimeManager:
    """Evaluates macro regime across top perpetuals and applies dynamic strategy tuning."""

    def __init__(self):
        self.current_regime: Optional[MarketRegimeInfo] = None
        self.history: List[MarketRegimeInfo] = []

    def evaluate_universe(self, df_top_50: pl.DataFrame) -> MarketRegimeInfo:
        """
        Analyze 50-asset market snapshot and classify market regime.
        Expects columns: ["coin", "price", "funding_apr", "vol_24h", "change_24h"]
        """
        total_count = len(df_top_50)
        if total_count == 0:
            return MarketRegimeInfo(
                regime="CHOPPY_MEAN_REVERTING_RANGE",
                breadth_pct=50.0,
                avg_change_24h=0.0,
                avg_funding_apr=10.0,
                volatility_score=0.0,
                high_funding_count=0,
                negative_funding_count=0,
                sentiment="Neutral baseline (no data)",
                active_tweaks=["Baseline allocations active"],
            )

        green_count = len(df_top_50.filter(pl.col("change_24h") > 0.0))
        breadth_pct = (green_count / total_count) * 100.0

        avg_change_24h = float(df_top_50["change_24h"].mean() or 0.0)
        avg_funding_apr = float(df_top_50["funding_apr"].mean() or 0.0)
        volatility_score = float(df_top_50["change_24h"].std() or 0.0)

        high_funding_count = len(df_top_50.filter(pl.col("funding_apr") > 45.0))
        negative_funding_count = len(df_top_50.filter(pl.col("funding_apr") < -10.0))

        # Regime classification logic
        tweaks = []
        if high_funding_count >= 5 or avg_funding_apr > 40.0:
            regime = "OVERHEATED_CROWDED_LONGS"
            sentiment = (
                f"Overheated Long Leverage ({high_funding_count} coins >+45% APR, "
                f"avg {avg_funding_apr:+.1f}% APR). Long liquidation cascade danger."
            )
            tweaks = [
                "Prioritizing Funding Fade (max 4 positions, cap 2.5x)",
                "Restricting Long Trend entries (conviction threshold raised to >= 90)",
                "Tightening trailing stops & MAE hard cuts",
            ]
        elif negative_funding_count >= 3 or avg_funding_apr < -5.0:
            regime = "NEGATIVE_FUNDING_SHORT_SQUEEZE"
            sentiment = (
                f"Negative Funding Short Crowd ({negative_funding_count} coins < -10% APR). "
                f"Explosive short-squeeze conditions detected."
            )
            tweaks = [
                "Long bias on negative funding coins to harvest short carry + squeeze momentum",
                "Completely suppressing short funding fades",
                "VWAP/OI breakout allocation expanded to 2.5x",
            ]
        elif breadth_pct >= 65.0 and avg_change_24h > 1.2:
            regime = "BULL_MOMENTUM_EXPANSION"
            sentiment = (
                f"Broad-Based Bull Expansion ({breadth_pct:.0f}% green, avg +{avg_change_24h:.1f}% 24h). "
                f"Institutional accumulation with healthy carry (+{avg_funding_apr:.1f}% APR)."
            )
            tweaks = [
                "SMC Trend Continuation max positions expanded to 4 (Alloc: 2.5x)",
                "VWAP/OI Momentum max positions expanded to 3 (Alloc: 2.0x)",
                "Trend profit target expanded to 3.0 R:R for runners",
                "Funding Fade entry threshold raised to +80% APR (avoid fighting trend)",
            ]
        elif breadth_pct <= 35.0 and avg_change_24h < -1.2:
            regime = "BEAR_MARKET_FLUSH"
            sentiment = (
                f"Broad-Based Bear Flush ({breadth_pct:.0f}% green, avg {avg_change_24h:.1f}% 24h). "
                f"Heavy selling pressure across universe."
            )
            tweaks = [
                "SMC Trend Continuation restricted to short setups and high-conviction (>92) longs",
                "Funding Fade prioritized for oversold bounces",
                "Tightening position risk to preserve capital",
            ]
        else:
            regime = "CHOPPY_MEAN_REVERTING_RANGE"
            sentiment = (
                f"Range-Bound Consolidation ({breadth_pct:.0f}% green, avg {avg_change_24h:+.1f}% 24h). "
                f"Support/resistance walls holding; low directional breakout follow-through."
            )
            tweaks = [
                "OrderBookImbalance allowed up to 2 concurrent scalps on depth skew walls",
                "Funding Fade prioritized for mean reversion (max 3 positions)",
                "VWAP breakout allocations trimmed to prevent whipsaw in range",
                "Profit targets tightened to 1.5 R:R to lock gains quickly",
            ]

        regime_info = MarketRegimeInfo(
            regime=regime,
            breadth_pct=breadth_pct,
            avg_change_24h=avg_change_24h,
            avg_funding_apr=avg_funding_apr,
            volatility_score=volatility_score,
            high_funding_count=high_funding_count,
            negative_funding_count=negative_funding_count,
            sentiment=sentiment,
            active_tweaks=tweaks,
        )

        self.current_regime = regime_info
        self.history.append(regime_info)
        if len(self.history) > 100:
            self.history.pop(0)

        return regime_info

    def apply_regime_to_engine(
        self,
        regime_info: MarketRegimeInfo,
        guard: Any,
        continuation_strat: Optional[Any] = None,
        funding_strat: Optional[Any] = None,
        scalp_strat: Optional[Any] = None,
        vwap_strat: Optional[Any] = None,
    ) -> List[str]:
        """
        Dynamically adjust strategy position limits, allocation caps, and strategy parameters.
        Returns list of applied adjustments.
        """
        applied = []
        regime = regime_info.regime

        if regime == "BULL_MOMENTUM_EXPANSION":
            guard.set_strategy_max_positions("TrendContinuationSMC", 4)
            guard.set_strategy_max_positions("VwapOiMomentum", 3)
            guard.set_strategy_max_positions("HourlyFundingFade", 1)
            guard.set_strategy_max_positions("OrderBookImbalance", 1)
            guard.set_strategy_allocation_cap("TrendContinuationSMC", 2.5)
            guard.set_strategy_allocation_cap("VwapOiMomentum", 2.0)
            guard.set_strategy_allocation_cap("HourlyFundingFade", 1.0)
            if continuation_strat:
                setattr(continuation_strat, "dynamic_rr_ratio", 3.0)
            if funding_strat:
                setattr(funding_strat, "dynamic_min_apr", 80.0)
            if vwap_strat:
                setattr(vwap_strat, "dynamic_oi_zscore_threshold", 0.8)
            if scalp_strat:
                setattr(scalp_strat, "dynamic_skew_threshold", 4.0)
            applied.append("SMC Trend: max 4 pos, cap 2.5x, TP 3.0 R:R")
            applied.append("VWAP/OI: max 3 pos, cap 2.0x, OI thresh 0.8σ")
            applied.append("Funding Fade: throttled to max 1 pos, min 80% APR")

        elif regime == "OVERHEATED_CROWDED_LONGS":
            guard.set_strategy_max_positions("HourlyFundingFade", 4)
            guard.set_strategy_max_positions("TrendContinuationSMC", 2)
            guard.set_strategy_max_positions("VwapOiMomentum", 1)
            guard.set_strategy_max_positions("OrderBookImbalance", 1)
            guard.set_strategy_allocation_cap("HourlyFundingFade", 2.5)
            guard.set_strategy_allocation_cap("TrendContinuationSMC", 1.2)
            if continuation_strat:
                setattr(continuation_strat, "dynamic_rr_ratio", 2.0)
            if funding_strat:
                setattr(funding_strat, "dynamic_min_apr", 45.0)
            if vwap_strat:
                setattr(vwap_strat, "dynamic_oi_zscore_threshold", 1.5)
            if scalp_strat:
                setattr(scalp_strat, "dynamic_skew_threshold", 3.5)
            applied.append("Funding Fade: max 4 pos, cap 2.5x, min 45% APR")
            applied.append("SMC Trend: throttled to max 2 pos, cap 1.2x")

        elif regime == "NEGATIVE_FUNDING_SHORT_SQUEEZE":
            guard.set_strategy_max_positions("HourlyFundingFade", 4)
            guard.set_strategy_max_positions("TrendContinuationSMC", 3)
            guard.set_strategy_max_positions("VwapOiMomentum", 3)
            guard.set_strategy_max_positions("OrderBookImbalance", 1)
            guard.set_strategy_allocation_cap("VwapOiMomentum", 2.5)
            guard.set_strategy_allocation_cap("HourlyFundingFade", 2.0)
            if continuation_strat:
                setattr(continuation_strat, "dynamic_rr_ratio", 2.5)
            if funding_strat:
                setattr(funding_strat, "dynamic_min_apr", 30.0)
            if vwap_strat:
                setattr(vwap_strat, "dynamic_oi_zscore_threshold", 0.9)
            if scalp_strat:
                setattr(scalp_strat, "dynamic_skew_threshold", 3.5)
            applied.append("Short Squeeze Mode: Funding Fade & VWAP max 4/3 pos, cap 2.5x")

        elif regime == "BEAR_MARKET_FLUSH":
            guard.set_strategy_max_positions("TrendContinuationSMC", 2)
            guard.set_strategy_max_positions("HourlyFundingFade", 3)
            guard.set_strategy_max_positions("VwapOiMomentum", 2)
            guard.set_strategy_max_positions("OrderBookImbalance", 1)
            guard.set_strategy_allocation_cap("TrendContinuationSMC", 1.2)
            guard.set_strategy_allocation_cap("HourlyFundingFade", 2.0)
            if continuation_strat:
                setattr(continuation_strat, "dynamic_rr_ratio", 1.5)
            if funding_strat:
                setattr(funding_strat, "dynamic_min_apr", 60.0)
            if vwap_strat:
                setattr(vwap_strat, "dynamic_oi_zscore_threshold", 1.6)
            if scalp_strat:
                setattr(scalp_strat, "dynamic_skew_threshold", 4.0)
            applied.append("Bear Flush Mode: Trend capped to 2 pos, Funding Fade prioritized for counter-trend")

        else:  # CHOPPY_MEAN_REVERTING_RANGE
            guard.set_strategy_max_positions("HourlyFundingFade", 3)
            guard.set_strategy_max_positions("TrendContinuationSMC", 2)
            guard.set_strategy_max_positions("OrderBookImbalance", 2)
            guard.set_strategy_max_positions("VwapOiMomentum", 1)
            guard.set_strategy_allocation_cap("HourlyFundingFade", 2.0)
            guard.set_strategy_allocation_cap("TrendContinuationSMC", 1.5)
            guard.set_strategy_allocation_cap("OrderBookImbalance", 1.0)
            if continuation_strat:
                setattr(continuation_strat, "dynamic_rr_ratio", 1.8)
            if funding_strat:
                setattr(funding_strat, "dynamic_min_apr", 50.0)
            if vwap_strat:
                setattr(vwap_strat, "dynamic_oi_zscore_threshold", 1.8)
            if scalp_strat:
                setattr(scalp_strat, "dynamic_skew_threshold", 3.0)
                setattr(scalp_strat, "dynamic_take_profit_ticks", 18)
            applied.append("Mean Reversion Mode: Funding Fade & Scalp prioritized (max 3/2 pos), Trend TP 1.8 R:R, Scalp TP 18t")

        return applied
