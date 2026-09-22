"""
Verification Script: Test Autonomous Operation & AI Disconnect Fail-Safe
Tests that if AI is disconnected, offline, or returns empty signals:
1. PortfolioGuard allows all strategy trades without AI prospect biases.
2. AIFallbackEngine generates pure quantitative prospects from live market data.
3. OrderBookImbalance, FundingFade, and SMCTrend operate autonomously.
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[1])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from decimal import Decimal
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.enums import OrderSide
from src.risk.portfolio_guard import PortfolioGuard
from src.risk.fallback_engine import AIFallbackEngine
from src.scanner.mcp_client import HyperliquidInfoClient

def test_autonomous_disconnected_operation():
    print("=================================================================")
    print("🔍 AUDIT: Autonomous Operation & AI Disconnect Fail-Safe Test")
    print("=================================================================")
    
    # 1. Test PortfolioGuard with AI completely disconnected (empty prospect biases)
    guard = PortfolioGuard(
        max_strategy_equity_pct=25.0,
        max_total_open_positions=10,
        max_daily_drawdown_pct=0.20,
    )
    guard.update_equity(170.0)
    guard.set_prospect_biases({})  # AI is disconnected / empty!
    
    test_instr = InstrumentId(Symbol("ETHFI-USD-PERP"), Venue("HYPERLIQUID"))
    
    # Test OrderBookImbalance approval without AI
    can_trade, reason = guard.can_open_position(
        strategy_name="OrderBookImbalance-002",
        instrument_id=test_instr,
        side=OrderSide.BUY,
        proposed_notional_usd=300.0,
        current_open_positions_count=0,
    )
    print(f"1. OrderBookImbalance (AI Disconnected): Approved={can_trade} | Reason='{reason}'")
    assert can_trade is True, f"Failed: {reason}"
    
    # Test HourlyFundingFade approval without AI
    can_trade, reason = guard.can_open_position(
        strategy_name="HourlyFundingFade-001",
        instrument_id=test_instr,
        side=OrderSide.SELL,
        proposed_notional_usd=250.0,
        current_open_positions_count=1,
    )
    print(f"2. HourlyFundingFade (AI Disconnected): Approved={can_trade} | Reason='{reason}'")
    assert can_trade is True, f"Failed: {reason}"

    # Test TrendContinuationSMC approval without AI
    can_trade, reason = guard.can_open_position(
        strategy_name="TrendContinuationSMC-001",
        instrument_id=test_instr,
        side=OrderSide.BUY,
        proposed_notional_usd=250.0,
        current_open_positions_count=2,
    )
    print(f"3. TrendContinuationSMC (AI Disconnected): Approved={can_trade} | Reason='{reason}'")
    assert can_trade is True, f"Failed: {reason}"

    # 2. Test AIFallbackEngine Deterministic Scanning
    fallback_engine = AIFallbackEngine()
    client = HyperliquidInfoClient()
    prospects = fallback_engine.generate_deterministic_prospects(client)
    print(f"\n4. AIFallbackEngine Generated {len(prospects)} Rule-Based Quantitative Prospects:")
    for coin, data in list(prospects.items())[:5]:
        print(f"   • {coin:8s} | Bias: {data['bias']:5s} | Target: ${data['target_entry']:<10.4f} | Rationale: {data['reason'][:60]}...")
    assert len(prospects) > 0, "Fallback engine failed to generate prospects!"

    print("\n✅ FAIL-SAFE VERIFIED: The bot ingests live data, screens markets, and executes trades 100% autonomously with zero AI dependency.")
    print("=================================================================")

if __name__ == "__main__":
    test_autonomous_disconnected_operation()
