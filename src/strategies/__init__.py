"""
Modular Strategy Actors for Hyperliquid Quant Node.
Includes:
  - TrendContinuationSMC (continuation.py)
  - HourlyFundingFade (funding_fade.py)
  - OrderBookImbalance (orderbook_scalp.py)
  - VwapOiMomentum (vwap_momentum.py)
"""

from src.strategies.continuation import TrendContinuationSMC, TrendContinuationConfig
from src.strategies.funding_fade import HourlyFundingFade, HourlyFundingFadeConfig
from src.strategies.orderbook_scalp import OrderBookImbalance, OrderBookImbalanceConfig
from src.strategies.vwap_momentum import VwapOiMomentum, VwapOiMomentumConfig

__all__ = [
    "TrendContinuationSMC",
    "TrendContinuationConfig",
    "HourlyFundingFade",
    "HourlyFundingFadeConfig",
    "OrderBookImbalance",
    "OrderBookImbalanceConfig",
    "VwapOiMomentum",
    "VwapOiMomentumConfig",
]
