"""
Pydantic Schemas for AI Decisions and Microstructure (src/utils/schemas.py)
Powered by jxnl/instructor for structured output enforcement.
"""

from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field


class RiskAuditDecision(BaseModel):
    """Structured decision emitted by the Groq Real-Time Risk Sentinel."""
    status: Literal["HEALTHY", "CAUTION", "ALERT", "CRITICAL"] = Field(
        description="Overall health evaluation of current active positions and orders"
    )
    action: Literal["NONE", "CANCEL_ORPHANS", "CLOSE_POSITION", "FLATTEN_ALL", "TIGHTEN_STOPS"] = Field(
        description="Autonomous corrective action required"
    )
    target_coins: List[str] = Field(
        default_factory=list,
        description="Coins requiring action (e.g. ['ENA', 'APEX'])"
    )
    reason: str = Field(
        description="Single concise sentence explaining the quantitative rationale"
    )
    confidence: float = Field(
        default=95.0,
        description="Confidence score (0.0 to 100.0)"
    )


class GroundedProspect(BaseModel):
    """Individual prospect analysis vetted by Google AI Studio with search grounding."""
    coin: str = Field(description="Perpetual symbol, e.g. 'ENA'")
    validated: bool = Field(description="True if safe to trade; False if toxic catalyst/exploit found")
    news_sentiment: Literal["BULLISH", "NEUTRAL", "TOXIC_CATALYST"] = Field(
        description="Real-time news and token catalyst sentiment"
    )
    adjusted_conviction: float = Field(
        description="Conviction score (0-100) after news/unlock analysis"
    )
    catalyst_verdict: str = Field(
        description="Concise summary citing recent news, unlocks, or on-chain developments"
    )
    citation: Optional[str] = Field(
        default="",
        description="Domain or headline source (e.g. 'coindesk.com', 'defillama.com')"
    )


class OrderBookMicrostructure(BaseModel):
    """Hummingbot-grade order book microstructure snapshot."""
    coin: str
    best_bid: float
    best_ask: float
    mid_price: float
    micro_price: float = Field(description="Volume-weighted fair mid-price")
    spread_bps: float = Field(description="Bid-ask spread in basis points")
    depth_imbalance_top: float = Field(description="Level 1 Order Book Imbalance [-1.0 to 1.0]")
    depth_imbalance_5: float = Field(description="Top 5 levels Order Book Imbalance [-1.0 to 1.0]")
    total_bid_depth_usd: float
    total_ask_depth_usd: float
    is_liquid: bool = Field(description="True if spread < 10 bps and top depth > $10,000")
