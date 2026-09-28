"""
Unit tests for Production-Grade TradeManager (tests/test_trade_manager.py).

Tests:
1. Peak / Highest Water Mark tracking (LONG and SHORT).
2. Breakeven ratchet (+1.0% ROI triggers +0.1% stop).
3. Trailing stop (+2.0% ROI triggers 0.75% trailing stop).
4. Max Adverse Excursion (MAE) hard cut (ROI <= -2.5% or loss <= -$2.00).
5. Stagnant trade exit (> 4 hours without moving > 0.3% ROI).
6. Automated trade journaling (reports/daily_pnl.md and reports/session_trades.json).
7. Integration with UnifiedEngine.
"""

import os
import json
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.risk.trade_manager import TradeManager, PositionTracker, TradeAction
from src.engine.unified_engine import UnifiedEngine


@pytest.fixture
def tmp_trade_manager(tmp_path):
    """Fixture providing a TradeManager instance with isolated temporary reports directory."""
    reports_dir = str(tmp_path / "reports")
    daily_pnl = str(tmp_path / "reports" / "daily_pnl.md")
    session_trades = str(tmp_path / "reports" / "session_trades.json")
    return TradeManager(
        reports_dir=reports_dir,
        daily_pnl_file=daily_pnl,
        session_trades_file=session_trades,
    )


# =========================================================================
# 1. Peak / Highest Water Mark Tracking Tests
# =========================================================================

def test_watermark_tracking_long(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Initial entry: LONG @ $100.00
    action = tm.update_position("BTC", "LONG", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)
    pos = tm.get_position("BTC")
    assert pos is not None
    assert pos.peak_price == 100.0
    assert pos.peak_roi == 0.0
    assert action.action == "HOLD"

    # Price rises to $105.00 (+5.0% ROI)
    tm.update_position("BTC", "LONG", size=1.0, entry_price=100.0, mark_price=105.0, current_time=t0 + 10)
    assert pos.peak_price == 105.0
    assert pytest.approx(pos.peak_roi, 0.01) == 5.0
    assert pytest.approx(pos.current_roi, 0.01) == 5.0

    # Price pulls back to $103.00 (+3.0% ROI) -> peak should remain 105.0
    tm.update_position("BTC", "LONG", size=1.0, entry_price=100.0, mark_price=103.0, current_time=t0 + 20)
    assert pos.peak_price == 105.0
    assert pytest.approx(pos.peak_roi, 0.01) == 5.0
    assert pytest.approx(pos.current_roi, 0.01) == 3.0

    # Price pushes higher to $108.00 (+8.0% ROI) -> peak updates to 108.0
    tm.update_position("BTC", "LONG", size=1.0, entry_price=100.0, mark_price=108.0, current_time=t0 + 30)
    assert pos.peak_price == 108.0
    assert pytest.approx(pos.peak_roi, 0.01) == 8.0


def test_watermark_tracking_short(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Initial entry: SHORT @ $100.00
    tm.update_position("ETH", "SHORT", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)
    pos = tm.get_position("ETH")
    assert pos is not None
    assert pos.peak_price == 100.0
    assert pos.peak_roi == 0.0

    # Price drops to $95.00 (+5.0% ROI for SHORT)
    tm.update_position("ETH", "SHORT", size=1.0, entry_price=100.0, mark_price=95.0, current_time=t0 + 10)
    assert pos.peak_price == 95.0
    assert pytest.approx(pos.peak_roi, 0.01) == 5.0
    assert pytest.approx(pos.current_roi, 0.01) == 5.0

    # Price bounces to $97.00 (+3.0% ROI) -> peak favorable price remains 95.0
    tm.update_position("ETH", "SHORT", size=1.0, entry_price=100.0, mark_price=97.0, current_time=t0 + 20)
    assert pos.peak_price == 95.0
    assert pytest.approx(pos.peak_roi, 0.01) == 5.0
    assert pytest.approx(pos.current_roi, 0.01) == 3.0

    # Price drops to $92.00 (+8.0% ROI) -> peak favorable updates to 92.0
    tm.update_position("ETH", "SHORT", size=1.0, entry_price=100.0, mark_price=92.0, current_time=t0 + 30)
    assert pos.peak_price == 92.0
    assert pytest.approx(pos.peak_roi, 0.01) == 8.0


# =========================================================================
# 2. Breakeven Ratchet Tests
# =========================================================================

def test_breakeven_ratchet_long(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Entry: LONG @ $100.00
    tm.update_position("SOL", "LONG", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)
    pos = tm.get_position("SOL")
    assert pos.breakeven_triggered is False
    assert pos.stop_price is None

    # Price reaches $100.80 (+0.8% ROI) -> Below 1.0% trigger, no breakeven yet
    action = tm.update_position("SOL", "LONG", size=1.0, entry_price=100.0, mark_price=100.80, current_time=t0 + 10)
    assert pos.breakeven_triggered is False
    assert action.should_close is False

    # Price reaches $101.20 (+1.2% ROI >= +1.0%) -> Breakeven triggers, stop at +0.1% = $100.10
    action = tm.update_position("SOL", "LONG", size=1.0, entry_price=100.0, mark_price=101.20, current_time=t0 + 20)
    assert pos.breakeven_triggered is True
    assert pytest.approx(pos.stop_price, 0.001) == 100.10
    assert action.should_close is False

    # Price dips to $100.20 (above $100.10 stop) -> HOLD
    action = tm.update_position("SOL", "LONG", size=1.0, entry_price=100.0, mark_price=100.20, current_time=t0 + 30)
    assert action.should_close is False

    # Price dips to $100.10 (hits stop) at t0 + 40 (< 90s min holding period) -> Suppressed with HOLD
    action = tm.update_position("SOL", "LONG", size=1.0, entry_price=100.0, mark_price=100.10, current_time=t0 + 40)
    assert action.should_close is False
    assert "HOLD (Min holding period active: 40s / 90s)" in action.reason

    # Price remains at $100.10 at t0 + 100 (> 90s min holding period) -> CLOSE
    action = tm.update_position("SOL", "LONG", size=1.0, entry_price=100.0, mark_price=100.10, current_time=t0 + 100)
    assert action.should_close is True
    assert "Breakeven ratchet triggered" in action.reason


def test_breakeven_ratchet_short(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Entry: SHORT @ $100.00
    tm.update_position("AVAX", "SHORT", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)
    pos = tm.get_position("AVAX")

    # Price drops to $98.80 (+1.2% ROI >= +1.0%) -> Breakeven triggers, stop at +0.1% = $99.90
    action = tm.update_position("AVAX", "SHORT", size=1.0, entry_price=100.0, mark_price=98.80, current_time=t0 + 10)
    assert pos.breakeven_triggered is True
    assert pytest.approx(pos.stop_price, 0.001) == 99.90
    assert action.should_close is False

    # Price rises to $99.80 (below $99.90 stop) -> HOLD
    action = tm.update_position("AVAX", "SHORT", size=1.0, entry_price=100.0, mark_price=99.80, current_time=t0 + 20)
    assert action.should_close is False

    # Price rises to $99.95 (breaches stop) at t0 + 30 (< 90s min holding period) -> Suppressed with HOLD
    action = tm.update_position("AVAX", "SHORT", size=1.0, entry_price=100.0, mark_price=99.95, current_time=t0 + 30)
    assert action.should_close is False
    assert "HOLD (Min holding period active: 30s / 90s)" in action.reason

    # Price rises to $99.95 at t0 + 100 (> 90s min holding period) -> CLOSE
    action = tm.update_position("AVAX", "SHORT", size=1.0, entry_price=100.0, mark_price=99.95, current_time=t0 + 100)
    assert action.should_close is True
    assert "Breakeven ratchet triggered" in action.reason


# =========================================================================
# 3. Trailing Stop Tests
# =========================================================================

def test_trailing_stop_long(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Entry: LONG @ $100.00
    tm.update_position("BTC", "LONG", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)

    # Price rises to $103.00 (+3.0% ROI >= +2.0%)
    # Trailing stop activated: trails 0.75% behind peak ($103.00 * (1 - 0.0075) = $102.2275)
    action = tm.update_position("BTC", "LONG", size=1.0, entry_price=100.0, mark_price=103.0, current_time=t0 + 10)
    pos = tm.get_position("BTC")
    assert pos.trailing_stop_triggered is True
    assert pytest.approx(pos.stop_price, 0.001) == 102.2275
    assert action.should_close is False

    # Price pushes further to $105.00 (+5.0% ROI)
    # Stop ratchets up to $105.00 * (1 - 0.0075) = $104.2125
    tm.update_position("BTC", "LONG", size=1.0, entry_price=100.0, mark_price=105.0, current_time=t0 + 20)
    assert pytest.approx(pos.stop_price, 0.001) == 104.2125

    # Price drops to $104.50 (above stop of 104.2125) -> HOLD
    action = tm.update_position("BTC", "LONG", size=1.0, entry_price=100.0, mark_price=104.50, current_time=t0 + 30)
    assert action.should_close is False
    assert pytest.approx(pos.stop_price, 0.001) == 104.2125  # stop must NOT move down!

    # Price drops to $104.10 (breaches stop) at t0 + 40 (< 90s min holding period) -> Suppressed with HOLD
    action = tm.update_position("BTC", "LONG", size=1.0, entry_price=100.0, mark_price=104.10, current_time=t0 + 40)
    assert action.should_close is False
    assert "HOLD (Min holding period active: 40s / 90s)" in action.reason

    # Price drops to $104.10 at t0 + 100 (> 90s min holding period) -> CLOSE
    action = tm.update_position("BTC", "LONG", size=1.0, entry_price=100.0, mark_price=104.10, current_time=t0 + 100)
    assert action.should_close is True
    assert "Trailing stop triggered" in action.reason


def test_trailing_stop_short(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Entry: SHORT @ $100.00
    tm.update_position("ETH", "SHORT", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)

    # Price drops to $97.00 (+3.0% ROI >= +2.0%)
    # Trailing stop activated: trails 0.75% behind peak ($97.00 * (1 + 0.0075) = $97.7275)
    action = tm.update_position("ETH", "SHORT", size=1.0, entry_price=100.0, mark_price=97.0, current_time=t0 + 10)
    pos = tm.get_position("ETH")
    assert pos.trailing_stop_triggered is True
    assert pytest.approx(pos.stop_price, 0.001) == 97.7275
    assert action.should_close is False

    # Price drops to $95.00 (+5.0% ROI)
    # Stop ratchets down to $95.00 * (1 + 0.0075) = $95.7125
    tm.update_position("ETH", "SHORT", size=1.0, entry_price=100.0, mark_price=95.0, current_time=t0 + 20)
    assert pytest.approx(pos.stop_price, 0.001) == 95.7125

    # Price bounces to $95.50 (below stop of 95.7125) -> HOLD
    action = tm.update_position("ETH", "SHORT", size=1.0, entry_price=100.0, mark_price=95.50, current_time=t0 + 30)
    assert action.should_close is False
    assert pytest.approx(pos.stop_price, 0.001) == 95.7125  # stop must NOT move up!

    # Price bounces to $95.80 (breaches stop) at t0 + 40 (< 90s min holding period) -> Suppressed with HOLD
    action = tm.update_position("ETH", "SHORT", size=1.0, entry_price=100.0, mark_price=95.80, current_time=t0 + 40)
    assert action.should_close is False
    assert "HOLD (Min holding period active: 40s / 90s)" in action.reason

    # Price bounces to $95.80 at t0 + 100 (> 90s min holding period) -> CLOSE
    action = tm.update_position("ETH", "SHORT", size=1.0, entry_price=100.0, mark_price=95.80, current_time=t0 + 100)
    assert action.should_close is True
    assert "Trailing stop triggered" in action.reason


# =========================================================================
# 4. Max Adverse Excursion (MAE) Hard Cut Tests
# =========================================================================

def test_mae_hard_cut_by_roi(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Entry: LONG @ $100.00, size 0.5 ($50 notional)
    tm.update_position("SOL", "LONG", size=0.5, entry_price=100.0, mark_price=100.0, current_time=t0)

    # Price drops to $97.40 (ROI = -2.6% <= -2.5%, PnL = -$1.30)
    action = tm.update_position("SOL", "LONG", size=0.5, entry_price=100.0, mark_price=97.40, current_time=t0 + 10)
    assert action.should_close is True
    assert "MAE hard cut" in action.reason
    assert "ROI -2.60%" in action.reason


def test_mae_hard_cut_by_loss_usd(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Entry: LONG @ $100.00, size 10.0 ($1000 notional)
    tm.update_position("SOL", "LONG", size=10.0, entry_price=100.0, mark_price=100.0, current_time=t0)

    # Price drops to $99.75 (ROI = -0.25%, but PnL = -$2.50 <= -$2.00)
    action = tm.update_position("SOL", "LONG", size=10.0, entry_price=100.0, mark_price=99.75, current_time=t0 + 10)
    assert action.should_close is True
    assert "MAE hard cut" in action.reason
    assert "loss $-2.50" in action.reason


def test_mae_hard_cut_short(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Entry: SHORT @ $100.00, size 1.0
    tm.update_position("BTC", "SHORT", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)

    # Price rises to $102.60 (ROI = -2.6% <= -2.5%, PnL = -$2.60 <= -$2.00)
    action = tm.update_position("BTC", "SHORT", size=1.0, entry_price=100.0, mark_price=102.60, current_time=t0 + 10)
    assert action.should_close is True
    assert "MAE hard cut" in action.reason


# =========================================================================
# 5. Stagnant Trade Exit Tests
# =========================================================================

def test_stagnant_trade_exit(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Entry: LONG @ $100.00
    tm.update_position("NEAR", "LONG", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)

    # Case 1: Open 3.5 hours (< 4 hours) with ROI +0.15% -> HOLD
    action = tm.update_position("NEAR", "LONG", size=1.0, entry_price=100.0, mark_price=100.15, current_time=t0 + (3.5 * 3600))
    assert action.should_close is False

    # Case 2: Open 4.5 hours (> 4 hours) with ROI +0.15% (<= 0.3% ROI) -> CLOSE
    action = tm.update_position("NEAR", "LONG", size=1.0, entry_price=100.0, mark_price=100.15, current_time=t0 + (4.5 * 3600))
    assert action.should_close is True
    assert "Stagnant trade exit" in action.reason


def test_stagnant_trade_exit_negative_flat(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Entry: SHORT @ $100.00
    tm.update_position("SUI", "SHORT", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)

    # Open 4.2 hours with ROI -0.10% (within +/- 0.3% ROI) -> CLOSE
    action = tm.update_position("SUI", "SHORT", size=1.0, entry_price=100.0, mark_price=100.10, current_time=t0 + (4.2 * 3600))
    assert action.should_close is True
    assert "Stagnant trade exit" in action.reason


def test_stagnant_trade_not_triggered_when_moving(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Entry: LONG @ $100.00
    tm.update_position("LINK", "LONG", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)

    # Open 4.5 hours with ROI +0.70% (> 0.3% ROI) -> HOLD
    action = tm.update_position("LINK", "LONG", size=1.0, entry_price=100.0, mark_price=100.70, current_time=t0 + (4.5 * 3600))
    assert action.should_close is False


# =========================================================================
# 6. Automated Trade Journaling Tests
# =========================================================================

def test_automated_trade_journaling(tmp_trade_manager):
    tm = tmp_trade_manager

    # Record closed trade 1: LONG BTC
    # Entry: 60,000, Exit: 61,200, Size: 0.05
    # Gross PnL = (61200 - 60000) * 0.05 = 60.0
    # Fees = (60000*0.05 + 61200*0.05) * 0.00035 = 6060 * 0.00035 = 2.121 -> 2.12
    # Net PnL = 60.0 - 2.12 = 57.88
    rec1 = tm.record_closed_trade(
        coin="BTC",
        side="LONG",
        size=0.05,
        entry=60000.0,
        exit=61200.0,
        duration=3600,
        pnl=60.0,
        roi=2.0,
        strategy="TrendContinuationSMC",
        reason="Trailing stop triggered",
    )

    # Check returned record keys
    required_keys = [
        "coin", "side", "size", "entry", "exit", "duration",
        "pnl", "gross_pnl", "fees", "net_pnl", "roi", "strategy", "reason"
    ]
    for k in required_keys:
        assert k in rec1

    assert rec1["coin"] == "BTC"
    assert rec1["side"] == "LONG"
    assert rec1["gross_pnl"] == 60.0
    assert rec1["fees"] == 2.12
    assert rec1["net_pnl"] == 57.88
    assert rec1["pnl"] == 57.88
    assert rec1["roi"] == 2.0
    assert rec1["duration"] == "1h 0m 0s"

    # Verify session_trades.json
    assert os.path.exists(tm.session_trades_file)
    with open(tm.session_trades_file, "r") as f:
        trades = json.load(f)
    assert len(trades) == 1
    assert trades[0]["coin"] == "BTC"
    assert trades[0]["gross_pnl"] == 60.0
    assert trades[0]["fees"] == 2.12
    assert trades[0]["net_pnl"] == 57.88
    assert trades[0]["strategy"] == "TrendContinuationSMC"

    # Verify daily_pnl.md has Gross PnL, Fees, Net PnL columns
    assert os.path.exists(tm.daily_pnl_file)
    with open(tm.daily_pnl_file, "r") as f:
        md_content = f.read()
    assert "# Daily PnL & Trade Journal" in md_content
    assert "| Date/Time (UTC) | Coin | Side | Size | Entry Px | Exit Px | Duration | Gross PnL ($) | Fees ($) | Net PnL ($) | ROI (%) | Strategy | Reason |" in md_content
    assert "| BTC | LONG | 0.05 | $60,000.0000 | $61,200.0000 | 1h 0m 0s | +$60.00 | $2.12 | +$57.88 | +2.00% | TrendContinuationSMC | Trailing stop triggered |" in md_content

    # Record closed trade 2: SHORT ETH
    # Entry: 3000, Exit: 3030, Size: 1.0
    # Gross PnL = (3000 - 3030) * 1.0 = -30.0
    # Fees = (3000*1.0 + 3030*1.0) * 0.00035 = 6030 * 0.00035 = 2.1105 -> 2.11
    # Net PnL = -30.0 - 2.11 = -32.11
    rec2 = tm.record_closed_trade(
        coin="ETH",
        side="SHORT",
        size=1.0,
        entry=3000.0,
        exit=3030.0,
        duration=1800,
        pnl=-30.0,
        roi=-1.0,
        strategy="HourlyFundingFade",
        reason="MAE hard cut",
    )

    with open(tm.session_trades_file, "r") as f:
        trades = json.load(f)
    assert len(trades) == 2
    assert trades[1]["coin"] == "ETH"
    assert trades[1]["gross_pnl"] == -30.0
    assert trades[1]["fees"] == 2.11
    assert trades[1]["net_pnl"] == -32.11

    with open(tm.daily_pnl_file, "r") as f:
        md_content = f.read()
    assert "| ETH | SHORT | 1.0 | $3,000.0000 | $3,030.0000 | 0h 30m 0s | -$30.00 | $2.11 | -$32.11 | -1.00% | HourlyFundingFade | MAE hard cut |" in md_content

    # Verify cumulative fees in get_summary()
    summary = tm.get_summary()
    assert pytest.approx(summary["cumulative_fees"], 0.01) == 4.23


def test_close_and_journal_position(tmp_trade_manager):
    tm = tmp_trade_manager
    t0 = 1000.0

    # Register and track position
    tm.update_position("SOL", "LONG", size=2.0, entry_price=150.0, mark_price=153.0, strategy="TrendContinuationSMC", current_time=t0)
    assert tm.get_position("SOL") is not None

    # Close and journal
    # Entry: 150.0, Exit: 153.0, Size: 2.0
    # Gross PnL = (153 - 150) * 2.0 = 6.0
    # Fees = (150*2 + 153*2) * 0.00035 = 606 * 0.00035 = 0.2121 -> 0.21
    # Net PnL = 6.0 - 0.21 = 5.79
    rec = tm.close_and_journal_position(
        coin="SOL",
        exit_price=153.0,
        reason="Manual Web Cockpit Close",
        exit_time=t0 + 900,
    )
    # Position should be removed from active tracking
    assert tm.get_position("SOL") is None
    assert rec["coin"] == "SOL"
    assert rec["gross_pnl"] == 6.0
    assert rec["fees"] == 0.21
    assert rec["net_pnl"] == 5.79
    assert rec["roi"] == 2.0
    assert rec["duration"] == "0h 15m 0s"

    # Verify cooldown activated for SOL
    assert tm.is_in_cooldown("SOL", current_time=t0 + 900 + 10) is True
    assert tm.is_in_cooldown("SOL", current_time=t0 + 900 + 181) is False

    # Check persistence
    with open(tm.session_trades_file, "r") as f:
        trades = json.load(f)
    assert len(trades) == 1
    assert trades[0]["coin"] == "SOL"
    assert trades[0]["fees"] == 0.21


# =========================================================================
# 7. UnifiedEngine Integration Tests
# =========================================================================

def test_unified_engine_trade_manager_integration():
    """Verify that UnifiedEngine initializes TradeManager and exposes summary."""
    engine = UnifiedEngine(paper=True)
    assert hasattr(engine, "trade_manager")
    assert isinstance(engine.trade_manager, TradeManager)

    # Test get_state() contains trade_manager summary
    state = engine.get_state()
    assert "trade_manager" in state
    assert "active_count" in state["trade_manager"]
    assert state["trade_manager"]["active_count"] == 0


# =========================================================================
# 8. Missed Opportunities & Decision Logging Tests
# =========================================================================

def test_log_missed_opportunity_markdown_and_jsonl(tmp_trade_manager):
    """Test that log_missed_opportunity appends cleanly to Markdown table and JSONL."""
    tm = tmp_trade_manager

    # 1. Log first missed opportunity with full metadata
    rec1 = tm.log_missed_opportunity(
        coin="SOL",
        strategy="OrderBookImbalance",
        reason="Spread too wide: 0.12% > 0.08%",
        metrics={"spread_pct": 0.12, "max_spread": 0.08},
        retrospective_note="Price spiked +1.5% 30s later; consider relaxing spread threshold",
    )

    assert rec1["coin"] == "SOL"
    assert rec1["strategy"] == "OrderBookImbalance"
    assert rec1["reason"] == "Spread too wide: 0.12% > 0.08%"
    assert rec1["metrics"] == {"spread_pct": 0.12, "max_spread": 0.08}
    assert rec1["retrospective_note"] == "Price spiked +1.5% 30s later; consider relaxing spread threshold"
    assert rec1["type"] == "missed_opportunity"

    # Verify Markdown file content
    assert os.path.exists(tm.missed_opportunities_file)
    with open(tm.missed_opportunities_file, "r", encoding="utf-8") as f:
        md_content = f.read()

    assert "# Missed Opportunities & Decision Journal" in md_content
    assert "| Date/Time (UTC) | Coin | Strategy | Reason | Metrics/Conditions | Retrospective Note |" in md_content
    assert "| SOL | OrderBookImbalance | Spread too wide: 0.12% > 0.08% | spread_pct=0.12, max_spread=0.08 | Price spiked +1.5% 30s later; consider relaxing spread threshold |" in md_content

    # Verify JSONL file content
    assert os.path.exists(tm.decision_journal_file)
    with open(tm.decision_journal_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    assert len(lines) == 1
    item1 = json.loads(lines[0])
    assert item1["coin"] == "SOL"
    assert item1["strategy"] == "OrderBookImbalance"
    assert item1["metrics"]["spread_pct"] == 0.12

    # 2. Log second missed opportunity with metrics=None and default note
    rec2 = tm.log_missed_opportunity(
        coin="BTC-PERP",
        strategy="TrendContinuationSMC",
        reason="Macro regime filter blocked long",
    )

    assert rec2["coin"] == "BTC"
    assert rec2["metrics"] == {}
    assert rec2["retrospective_note"] == "-"

    # Verify second entry in Markdown
    with open(tm.missed_opportunities_file, "r", encoding="utf-8") as f:
        md_content = f.read()
    assert "| BTC | TrendContinuationSMC | Macro regime filter blocked long | - | - |" in md_content

    # Verify second entry in JSONL
    with open(tm.decision_journal_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    assert len(lines) == 2
    item2 = json.loads(lines[1])
    assert item2["coin"] == "BTC"
    assert item2["metrics"] == {}


def test_get_missed_opportunities(tmp_trade_manager, tmp_path):
    """Test get_missed_opportunities retrieval, limits, reverse ordering, and deduplication."""
    tm = tmp_trade_manager

    # Empty state
    assert tm.get_missed_opportunities() == []

    # Log 3 opportunities
    tm.log_missed_opportunity("SOL", "OrderBookImbalance", "Spread too wide", {"spread": 0.09})
    tm.log_missed_opportunity("ETH", "HourlyFundingFade", "Funding APR too low", {"funding": 0.02})
    tm.log_missed_opportunity("AVAX", "TrendContinuationSMC", "EMA trend misaligned", {"ema": 200})

    # Retrieve all
    all_opps = tm.get_missed_opportunities()
    assert len(all_opps) == 3
    assert all_opps[0]["coin"] == "SOL"
    assert all_opps[1]["coin"] == "ETH"
    assert all_opps[2]["coin"] == "AVAX"

    # Test limit
    limited = tm.get_missed_opportunities(limit=2)
    assert len(limited) == 2
    assert limited[0]["coin"] == "ETH"
    assert limited[1]["coin"] == "AVAX"

    # Test reverse order (most recent first)
    reversed_opps = tm.get_missed_opportunities(limit=2, reverse=True)
    assert len(reversed_opps) == 2
    assert reversed_opps[0]["coin"] == "AVAX"
    assert reversed_opps[1]["coin"] == "ETH"

    # Test limit <= 0
    assert tm.get_missed_opportunities(limit=0) == []

    # Test re-initialization and deduplication from disk
    tm2 = TradeManager(
        reports_dir=tm.reports_dir,
        daily_pnl_file=tm.daily_pnl_file,
        session_trades_file=tm.session_trades_file,
        missed_opportunities_file=tm.missed_opportunities_file,
        decision_journal_file=tm.decision_journal_file,
    )
    loaded = tm2.get_missed_opportunities()
    assert len(loaded) == 3
    assert loaded[0]["coin"] == "SOL"
    assert loaded[2]["coin"] == "AVAX"


def test_get_closed_trades(tmp_trade_manager):
    """Test get_closed_trades retrieval, limits, reverse ordering, and persistence."""
    tm = tmp_trade_manager

    # Empty state
    assert tm.get_closed_trades() == []

    # Record 2 closed trades
    tm.record_closed_trade(
        coin="BTC",
        side="LONG",
        size=0.1,
        entry=65000.0,
        exit=66000.0,
        duration=1200,
        pnl=100.0,
        roi=1.54,
        strategy="TrendContinuationSMC",
        reason="Trailing stop triggered",
    )
    tm.record_closed_trade(
        coin="ETH",
        side="SHORT",
        size=1.0,
        entry=3500.0,
        exit=3450.0,
        duration=600,
        pnl=50.0,
        roi=1.43,
        strategy="HourlyFundingFade",
        reason="Breakeven ratchet triggered",
    )

    # Test get_closed_trades returns both trades
    trades = tm.get_closed_trades()
    assert len(trades) == 2
    assert trades[0]["coin"] == "BTC"
    assert trades[0]["gross_pnl"] == 100.0
    assert pytest.approx(trades[0]["net_pnl"], 0.01) == 95.41
    assert trades[1]["coin"] == "ETH"
    assert trades[1]["gross_pnl"] == 50.0
    assert pytest.approx(trades[1]["net_pnl"], 0.01) == 47.57

    # Test limit
    limited = tm.get_closed_trades(limit=1)
    assert len(limited) == 1
    assert limited[0]["coin"] == "ETH"

    # Test reverse order (most recent first)
    rev_trades = tm.get_closed_trades(limit=2, reverse=True)
    assert len(rev_trades) == 2
    assert rev_trades[0]["coin"] == "ETH"
    assert rev_trades[1]["coin"] == "BTC"

    # Test limit <= 0
    assert tm.get_closed_trades(limit=0) == []

    # Test re-initialization from session_trades.json
    tm2 = TradeManager(
        reports_dir=tm.reports_dir,
        daily_pnl_file=tm.daily_pnl_file,
        session_trades_file=tm.session_trades_file,
        missed_opportunities_file=tm.missed_opportunities_file,
        decision_journal_file=tm.decision_journal_file,
    )
    loaded_trades = tm2.get_closed_trades()
    assert len(loaded_trades) == 2
    assert loaded_trades[0]["coin"] == "BTC"
    assert loaded_trades[1]["coin"] == "ETH"


def test_unified_engine_missed_opportunities_and_closed_trades():
    """Verify UnifiedEngine convenience delegates for missed opportunities and closed trades."""
    engine = UnifiedEngine(paper=True)

    # Test log_missed_opportunity delegate
    rec = engine.log_missed_opportunity(
        coin="SOL",
        strategy="OrderBookImbalance",
        reason="L2 imbalance threshold not met",
        metrics={"bid_ask_ratio": 1.1},
        retrospective_note="Wait for ratio > 1.5",
    )
    assert rec["coin"] == "SOL"

    # Test get_missed_opportunities delegate
    missed = engine.get_missed_opportunities(limit=10)
    assert len(missed) >= 1
    assert any(m["coin"] == "SOL" for m in missed)

    # Test get_closed_trades delegate
    closed = engine.get_closed_trades(limit=10)
    assert isinstance(closed, list)

    # Test is_in_cooldown and find_capital_rotation_candidate delegates
    assert engine.is_in_cooldown("NONEXISTENT") is False
    assert engine.find_capital_rotation_candidate("BTC", 0.8) is None


# =========================================================================
# 9. Fees and Rates Accounting Tests
# =========================================================================

def test_fee_accounting_and_rates(tmp_trade_manager):
    """Test standard 3.5 bps taker fee per side, gross_pnl, net_pnl, and cumulative fees."""
    tm = tmp_trade_manager
    assert tm.taker_fee_pct == 0.035

    # 1. LONG trade:
    # Entry: 100.0, Exit: 110.0, Size: 5.0
    # Notional: entry = $500, exit = $550 -> total notional = $1050
    # Fees = $1050 * 0.00035 = $0.3675 -> $0.37
    # Gross PnL = (110 - 100) * 5.0 = $50.00
    # Net PnL = 50.00 - 0.37 = $49.63
    rec_long = tm.record_closed_trade(
        coin="SOL",
        side="LONG",
        size=5.0,
        entry=100.0,
        exit=110.0,
        duration=300,
        strategy="TrendContinuationSMC",
        reason="Take profit target",
    )
    assert rec_long["gross_pnl"] == 50.00
    assert rec_long["fees"] == 0.37
    assert rec_long["net_pnl"] == 49.63
    assert rec_long["pnl"] == 49.63

    # 2. SHORT trade:
    # Entry: 200.0, Exit: 190.0, Size: 2.0
    # Notional: entry = $400, exit = $380 -> total notional = $780
    # Fees = $780 * 0.00035 = $0.273 -> $0.27
    # Gross PnL = (200 - 190) * 2.0 = $20.00
    # Net PnL = 20.00 - 0.27 = $19.73
    rec_short = tm.record_closed_trade(
        coin="AVAX",
        side="SHORT",
        size=2.0,
        entry=200.0,
        exit=190.0,
        duration=600,
        strategy="HourlyFundingFade",
        reason="Funding reset",
    )
    assert rec_short["gross_pnl"] == 20.00
    assert rec_short["fees"] == 0.27
    assert rec_short["net_pnl"] == 19.73

    # 3. Cumulative fees tracking across session
    summary = tm.get_summary()
    expected_fees = round(0.37 + 0.27, 2)
    assert summary["cumulative_fees"] == expected_fees

    # 4. Persistence verification
    with open(tm.session_trades_file, "r") as f:
        trades = json.load(f)
    assert len(trades) == 2
    assert trades[0]["fees"] == 0.37
    assert trades[1]["fees"] == 0.27

    with open(tm.daily_pnl_file, "r") as f:
        md = f.read()
    assert "| SOL | LONG | 5.0 | $100.0000 | $110.0000 | 0h 5m 0s | +$50.00 | $0.37 | +$49.63 | +10.00% | TrendContinuationSMC | Take profit target |" in md
    assert "| AVAX | SHORT | 2.0 | $200.0000 | $190.0000 | 0h 10m 0s | +$20.00 | $0.27 | +$19.73 | +5.00% | HourlyFundingFade | Funding reset |" in md


# =========================================================================
# 10. Anti-Churn Min Holding Period & MAE Priority Tests
# =========================================================================

def test_min_holding_period_and_mae_priority(tmp_trade_manager):
    """Test that non-emergency exits are suppressed before 90s, while MAE hard stop executes immediately."""
    tm = tmp_trade_manager
    assert tm.min_holding_seconds == 90.0
    t0 = 1000.0

    # Register position: LONG @ $100.00
    tm.update_position("NEAR", "LONG", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)

    # 1. Non-emergency exit signaled before 90s (at t0 + 30s) -> Suppressed with HOLD
    action = tm.update_position(
        "NEAR", "LONG", size=1.0, entry_price=100.0, mark_price=100.05,
        current_time=t0 + 30, exit_signal="Orderbook flicker",
    )
    assert action.should_close is False
    assert action.action == "HOLD"
    assert "HOLD (Min holding period active: 30s / 90s)" in action.reason

    # check_exit_allowed helper before 90s
    allowed, reason = tm.check_exit_allowed("NEAR", is_emergency=False, current_time=t0 + 30)
    assert allowed is False
    assert "HOLD (Min holding period active: 30s / 90s)" in reason

    # Emergency check is allowed even before 90s
    allowed, reason = tm.check_exit_allowed("NEAR", is_emergency=True, current_time=t0 + 30)
    assert allowed is True

    # 2. Non-emergency exit signaled after 90s (at t0 + 95s) -> CLOSE executes
    action = tm.update_position(
        "NEAR", "LONG", size=1.0, entry_price=100.0, mark_price=100.05,
        current_time=t0 + 95, exit_signal="Orderbook depth wall collapsed",
    )
    assert action.should_close is True
    assert action.action == "CLOSE"
    assert action.reason == "Orderbook depth wall collapsed"

    allowed, reason = tm.check_exit_allowed("NEAR", is_emergency=False, current_time=t0 + 95)
    assert allowed is True

    # 3. MAE hard stop at 15s (< 90s) -> ALWAYS executes immediately!
    tm.update_position("INJ", "LONG", size=1.0, entry_price=100.0, mark_price=100.0, current_time=t0)
    # Price drops to $97.00 (ROI = -3.0% <= -2.5%) at t0 + 15s
    action_mae = tm.update_position(
        "INJ", "LONG", size=1.0, entry_price=100.0, mark_price=97.0, current_time=t0 + 15
    )
    assert action_mae.should_close is True
    assert action_mae.action == "CLOSE"
    assert "MAE hard cut" in action_mae.reason


# =========================================================================
# 11. Anti-Churn Re-entry Cooldown Tests
# =========================================================================

def test_anti_churn_reentry_cooldown(tmp_trade_manager):
    """Test that closed positions enter a 180s cooldown and is_in_cooldown correctly enforces it."""
    tm = tmp_trade_manager
    assert tm.reentry_cooldown_seconds == 180.0
    t0 = 1000.0

    # Before close: not in cooldown
    assert tm.is_in_cooldown("SUI", current_time=t0) is False

    # Close position at t0
    tm.close_and_journal_position("SUI", exit_price=10.0, reason="Manual Close", exit_time=t0)

    # In cooldown at t0 + 10s
    assert tm.is_in_cooldown("SUI", current_time=t0 + 10) is True
    assert pytest.approx(tm.get_cooldown_remaining("SUI", current_time=t0 + 10), 0.1) == 170.0

    # In cooldown at t0 + 179s
    assert tm.is_in_cooldown("SUI", current_time=t0 + 179) is True
    assert pytest.approx(tm.get_cooldown_remaining("SUI", current_time=t0 + 179), 0.1) == 1.0

    # Cooldown expires at t0 + 181s
    assert tm.is_in_cooldown("SUI", current_time=t0 + 181) is False
    assert tm.get_cooldown_remaining("SUI", current_time=t0 + 181) == 0.0

    # Test manual cooldown manipulation (set / clear)
    tm.set_cooldown("TIA", timestamp=t0)
    assert tm.is_in_cooldown("TIA", current_time=t0 + 50) is True
    tm.clear_cooldown("TIA")
    assert tm.is_in_cooldown("TIA", current_time=t0 + 50) is False


# =========================================================================
# 12. Opportunity-Cost Capital Rotation Tests
# =========================================================================

def test_capital_rotation_candidate_selection(tmp_trade_manager):
    """Test find_capital_rotation_candidate identifies the weakest stagnant holding when at/near max positions."""
    tm = tmp_trade_manager
    t0 = 1000.0

    # Scenario 1: Not at or near max positions (only 1 position with max_positions=10)
    tm.update_position("SEI", "LONG", size=100.0, entry_price=1.00, mark_price=1.00, current_time=t0)
    # ROI = -0.1% (within [-0.3%, +0.2%]), open 400s (> 300s)
    tm.update_position("SEI", "LONG", size=100.0, entry_price=1.00, mark_price=0.999, current_time=t0 + 400)

    cand = tm.find_capital_rotation_candidate("TAO", 0.90, max_positions=10, current_time=t0 + 400)
    assert cand is None  # Plenty of capital available, no need to rotate!

    # Scenario 2: At max positions (max_positions=2, both slots occupied)
    # Position 1: SEI open 400s, ROI = -0.2% (stagnant)
    tm.update_position("SEI", "LONG", size=100.0, entry_price=1.00, mark_price=0.998, current_time=t0 + 400)

    # Position 2: SUI open 400s, ROI = +0.1% (stagnant)
    tm.update_position("SUI", "LONG", size=100.0, entry_price=2.00, mark_price=2.00, current_time=t0)
    tm.update_position("SUI", "LONG", size=100.0, entry_price=2.00, mark_price=2.002, current_time=t0 + 400)

    # Both are stagnant, but SEI is the weakest (ROI -0.2% < +0.1%)
    cand = tm.find_capital_rotation_candidate("TAO", 0.95, max_positions=2, current_time=t0 + 400)
    assert cand is not None
    coin_to_close, reason = cand
    assert coin_to_close == "SEI"
    assert reason == "Capital rotation: reallocating stagnant capital to high-conviction prospect TAO"

    # Scenario 3: Candidate holding is NOT stagnant (ROI = +1.5% > +0.2%)
    # SEI moves up to +1.5% ROI
    tm.update_position("SEI", "LONG", size=100.0, entry_price=1.00, mark_price=1.015, current_time=t0 + 400)
    # Now only SUI is stagnant (ROI +0.1%)
    cand = tm.find_capital_rotation_candidate("TAO", 0.95, max_positions=2, current_time=t0 + 400)
    assert cand is not None
    coin_to_close, reason = cand
    assert coin_to_close == "SUI"

    # Scenario 4: Holding duration is too short (< 300s)
    cand_early = tm.find_capital_rotation_candidate("TAO", 0.95, max_positions=2, current_time=t0 + 200)
    assert cand_early is None

    # Scenario 5: Prospect is already actively held
    cand_duplicate = tm.find_capital_rotation_candidate("SEI", 0.95, max_positions=2, current_time=t0 + 400)
    assert cand_duplicate is None


def test_atr_dynamic_trailing_stop(tmp_trade_manager):
    """Test ATR-scaled dynamic trailing stop padding based on coin volatility."""
    tm = tmp_trade_manager
    t0 = 1000.0

    # High volatility altcoin: atr_pct = 1.2% -> trailing distance should scale to 1.5 * 1.2 = 1.8%
    tm.register_position("PEPE", "LONG", size=1000.0, entry_price=10.0, entry_time=t0, atr_pct=1.2)
    pos = tm.get_position("PEPE")
    assert pos is not None
    assert pos.dynamic_trailing_distance_pct == 1.8

    # Price moves up to +3.0% ROI ($10.30)
    action1 = tm.update_position("PEPE", "LONG", size=1000.0, entry_price=10.0, mark_price=10.30, current_time=t0 + 100, atr_pct=1.2)
    assert action1.action == "HOLD"
    assert pos.trailing_stop_triggered is True
    # Stop price should be 10.30 * (1 - 0.018) = 10.1146
    assert pytest.approx(pos.stop_price, 0.01) == 10.1146

    # Price pulls back to 10.15 (still above 10.1146) -> Should HOLD
    action2 = tm.update_position("PEPE", "LONG", size=1000.0, entry_price=10.0, mark_price=10.15, current_time=t0 + 120, atr_pct=1.2)
    assert action2.action == "HOLD"

    # Price pulls back to 10.10 (below 10.1146, held > min_holding_seconds) -> Should CLOSE
    action3 = tm.update_position("PEPE", "LONG", size=1000.0, entry_price=10.0, mark_price=10.10, current_time=t0 + 130, atr_pct=1.2)
    assert action3.action == "CLOSE"
    assert action3.should_close is True
    assert "Trail: 1.8%" in action3.reason


def test_take_profit_target_exit(tmp_trade_manager):
    """Test Rule 2.5: Immediate take profit banking when ROI >= take_profit_roi_pct."""
    tm = tmp_trade_manager
    t0 = 1000.0

    # Register position with a 2.0% Take Profit target
    tm.register_position("SOL", "LONG", size=10.0, entry_price=100.0, entry_time=t0, take_profit_roi_pct=2.0)

    # Price moves to +1.5% ROI (not yet at TP, held > 90s) -> Should HOLD (or ratchet breakeven)
    action1 = tm.update_position("SOL", "LONG", size=10.0, entry_price=100.0, mark_price=101.5, current_time=t0 + 100)
    assert action1.action == "HOLD"

    # Price hits +2.2% ROI (exceeds 2.0% TP) -> Should CLOSE and bank profit
    action2 = tm.update_position("SOL", "LONG", size=10.0, entry_price=100.0, mark_price=102.2, current_time=t0 + 105)
    assert action2.action == "CLOSE"
    assert action2.should_close is True
    assert "Take Profit target reached: ROI +2.20%" in action2.reason


def test_extended_hold_profit_exit(tmp_trade_manager):
    """Test Rule 2.5: Extended hold profit lock when position held > 2h with ROI >= 1.5%."""
    tm = tmp_trade_manager
    tm.extended_hold_hours = 2.0
    tm.extended_hold_roi_pct = 1.5
    t0 = 1000.0

    # Register position with default 3.0% TP
    tm.register_position("ETH", "LONG", size=1.0, entry_price=3000.0, entry_time=t0, take_profit_roi_pct=3.0)

    # Position open for 1 hour with +1.8% ROI -> Should HOLD (under 2 hours)
    action1 = tm.update_position("ETH", "LONG", size=1.0, entry_price=3000.0, mark_price=3054.0, current_time=t0 + 3600)
    assert action1.action == "HOLD"

    # Position open for 2.5 hours with +1.8% ROI (>= 1.5% profit threshold) -> Should CLOSE to prevent round-trip
    action2 = tm.update_position("ETH", "LONG", size=1.0, entry_price=3000.0, mark_price=3054.0, current_time=t0 + 9000)
    assert action2.action == "CLOSE"
    assert action2.should_close is True
    assert "Extended hold profit lock" in action2.reason


