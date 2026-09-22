"""
Unit tests for Quantitative Performance Analytics and Dynamic Strategy Capital Allocation.
Tests:
  1. PerformanceAnalytics metrics calculation (win rate, profit factor, sharpe, expectancy, max DD, fees).
  2. PerformanceAnalytics markdown report generation.
  3. Dynamic Strategy Capital Allocation in PortfolioGuard:
     - Expansion to 35% - 40% for high win rate & positive PnL.
     - Throttling to 10% - 15% for low win rate or negative PnL.
     - Baseline 25% for neutral performance.
     - Sub-actor name resolution (e.g., 'TrendContinuationSMC-001' -> 'TrendContinuationSMC').
     - Enforcement of dynamic margin caps in can_open_position.
  4. UnifiedEngine integration with PerformanceAnalytics and dynamic risk allocation.
"""

import os
import json
import pytest
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.enums import OrderSide

from src.risk.performance_analytics import PerformanceAnalytics
from src.risk.portfolio_guard import PortfolioGuard
from src.engine.unified_engine import UnifiedEngine


def test_performance_analytics_empty():
    analytics = PerformanceAnalytics()
    metrics = analytics.get_metrics(trades=[])
    assert metrics["total_trades"] == 0
    assert metrics["winning_trades"] == 0
    assert metrics["losing_trades"] == 0
    assert metrics["win_rate_pct"] == 0.0
    assert metrics["profit_factor"] == 0.0
    assert metrics["sharpe_ratio"] == 0.0
    assert metrics["expectancy_usd"] == 0.0
    assert metrics["total_net_pnl"] == 0.0
    assert metrics["total_gross_pnl"] == 0.0
    assert metrics["total_fees_paid"] == 0.0
    assert metrics["max_drawdown_pct"] == 0.0


def test_performance_analytics_calculation(tmp_path):
    sample_trades = [
        {
            "coin": "BTC",
            "side": "LONG",
            "size": 1.0,
            "entry": 50000.0,
            "exit": 51000.0,
            "gross_pnl": 1000.0,
            "fees": 35.35,
            "net_pnl": 964.65,
            "strategy": "TrendContinuationSMC",
        },
        {
            "coin": "ETH",
            "side": "SHORT",
            "size": 10.0,
            "entry": 3000.0,
            "exit": 3050.0,
            "gross_pnl": -500.0,
            "fees": 21.175,
            "net_pnl": -521.175,
            "strategy": "TrendContinuationSMC",
        },
        {
            "coin": "SOL",
            "side": "LONG",
            "size": 50.0,
            "entry": 100.0,
            "exit": 110.0,
            "gross_pnl": 500.0,
            "fees": 3.675,
            "net_pnl": 496.325,
            "strategy": "OrderBookImbalance",
        },
    ]

    report_file = str(tmp_path / "test_session_trades.json")
    with open(report_file, "w") as f:
        json.dump(sample_trades, f)

    analytics = PerformanceAnalytics(session_trades_path=report_file)
    metrics = analytics.get_metrics()

    assert metrics["total_trades"] == 3
    assert metrics["winning_trades"] == 2
    assert metrics["losing_trades"] == 1
    assert round(metrics["win_rate_pct"], 1) == 66.7

    # Total net PnL: 964.65 - 521.175 + 496.325 = 939.80
    assert metrics["total_net_pnl"] == 939.80
    # Profit factor: (964.65 + 496.325) / 521.175 = 1460.975 / 521.175 = 2.80
    assert metrics["profit_factor"] == 2.80
    # Expectancy: 939.80 / 3 = 313.27
    assert metrics["expectancy_usd"] == 313.27
    # Total fees paid: 35.35 + 21.175 + 3.675 = 60.20
    assert metrics["total_fees_paid"] == 60.20
    # Avg win: 1460.975 / 2 = 730.49
    assert metrics["avg_win_usd"] == 730.49
    # Avg loss: 521.18 / 521.17 (float representation)
    assert abs(metrics["avg_loss_usd"] - 521.18) <= 0.02
    # Sharpe ratio > 0
    assert metrics["sharpe_ratio"] > 0


def test_performance_analytics_markdown_report(tmp_path):
    sample_trades = [
        {
            "coin": "BTC",
            "side": "LONG",
            "size": 1.0,
            "entry": 50000.0,
            "exit": 51000.0,
            "gross_pnl": 1000.0,
            "fees": 35.35,
            "net_pnl": 964.65,
            "roi": 2.0,
            "strategy": "TrendContinuationSMC",
            "timestamp": "2026-09-21 12:00:00",
            "reason": "Take Profit",
        },
        {
            "coin": "SOL",
            "side": "LONG",
            "size": 50.0,
            "entry": 100.0,
            "exit": 98.0,
            "gross_pnl": -100.0,
            "fees": 3.465,
            "net_pnl": -103.465,
            "roi": -2.0,
            "strategy": "OrderBookImbalance",
            "timestamp": "2026-09-21 12:30:00",
            "reason": "MAE hard cut",
        },
    ]
    out_md = str(tmp_path / "performance_report.md")
    analytics = PerformanceAnalytics()
    md_content = analytics.generate_markdown_report(trades=sample_trades, output_file=out_md)

    assert os.path.exists(out_md)
    assert "# Quantitative Performance Analytics Report" in md_content
    assert "TrendContinuationSMC" in md_content
    assert "OrderBookImbalance" in md_content
    assert "Total Closed Trades" in md_content
    assert "Win Rate" in md_content
    assert "Profit Factor" in md_content


def test_dynamic_allocation_expansion_and_throttling():
    guard = PortfolioGuard(max_strategy_equity_pct=0.25)
    guard.update_equity(10_000.0)

    # Initial caps should be baseline 0.25
    assert guard.get_strategy_allocation_cap("TrendContinuationSMC") == 0.25
    assert guard.get_strategy_allocation_cap("HourlyFundingFade") == 0.25

    # 1. High performance strategy (win rate 80% >= 75%, net PnL > 0) -> Expand to 40% (0.40)
    high_perf_trades = [
        {"strategy": "TrendContinuationSMC-001", "gross_pnl": 10.0, "fees": 1.0, "net_pnl": 9.0},
        {"strategy": "TrendContinuationSMC-001", "gross_pnl": 15.0, "fees": 1.0, "net_pnl": 14.0},
        {"strategy": "TrendContinuationSMC-001", "gross_pnl": 20.0, "fees": 1.0, "net_pnl": 19.0},
        {"strategy": "TrendContinuationSMC-001", "gross_pnl": 25.0, "fees": 1.0, "net_pnl": 24.0},
        {"strategy": "TrendContinuationSMC-001", "gross_pnl": -5.0, "fees": 1.0, "net_pnl": -6.0},
    ]

    # 2. Moderate high performance strategy (win rate 60% >= 60%, net PnL > 0) -> Expand to 35% (0.35)
    mod_high_trades = [
        {"strategy": "VwapOiMomentum", "gross_pnl": 10.0, "fees": 1.0, "net_pnl": 9.0},
        {"strategy": "VwapOiMomentum", "gross_pnl": 15.0, "fees": 1.0, "net_pnl": 14.0},
        {"strategy": "VwapOiMomentum", "gross_pnl": 20.0, "fees": 1.0, "net_pnl": 19.0},
        {"strategy": "VwapOiMomentum", "gross_pnl": -5.0, "fees": 1.0, "net_pnl": -6.0},
        {"strategy": "VwapOiMomentum", "gross_pnl": -5.0, "fees": 1.0, "net_pnl": -6.0},
    ]

    # 3. Poor performance strategy (win rate 20% < 25%) -> Throttle to 10% (0.10)
    poor_perf_trades = [
        {"strategy": "HourlyFundingFade", "gross_pnl": -10.0, "fees": 1.0, "net_pnl": -11.0},
        {"strategy": "HourlyFundingFade", "gross_pnl": -15.0, "fees": 1.0, "net_pnl": -16.0},
        {"strategy": "HourlyFundingFade", "gross_pnl": -20.0, "fees": 1.0, "net_pnl": -21.0},
        {"strategy": "HourlyFundingFade", "gross_pnl": -25.0, "fees": 1.0, "net_pnl": -26.0},
        {"strategy": "HourlyFundingFade", "gross_pnl": 5.0, "fees": 1.0, "net_pnl": 4.0},
    ]

    # 4. Moderate low performance strategy (win rate 33% < 40%) -> Throttle to 15% (0.15)
    mod_low_trades = [
        {"strategy": "OrderBookImbalance", "gross_pnl": 10.0, "fees": 1.0, "net_pnl": 9.0},
        {"strategy": "OrderBookImbalance", "gross_pnl": -10.0, "fees": 1.0, "net_pnl": -11.0},
        {"strategy": "OrderBookImbalance", "gross_pnl": -10.0, "fees": 1.0, "net_pnl": -11.0},
    ]

    # 5. Neutral performance strategy (50% win rate, net PnL > 0) -> Baseline 25% (0.25)
    neutral_trades = [
        {"strategy": "NeutralStrat", "gross_pnl": 20.0, "fees": 1.0, "net_pnl": 19.0},
        {"strategy": "NeutralStrat", "gross_pnl": -10.0, "fees": 1.0, "net_pnl": -11.0},
    ]

    all_trades = high_perf_trades + mod_high_trades + poor_perf_trades + mod_low_trades + neutral_trades
    caps = guard.update_dynamic_allocations(all_trades)

    # Verify scaling
    assert caps["TrendContinuationSMC"] == 0.40
    assert caps["TrendContinuationSMC-001"] == 0.40
    assert caps["VwapOiMomentum"] == 0.35
    assert caps["HourlyFundingFade"] == 0.10
    assert caps["OrderBookImbalance"] == 0.15
    assert caps["NeutralStrat"] == 0.25

    # Sub-actor lookup check
    assert guard.get_strategy_allocation_cap("TrendContinuationSMC-002") == 0.40
    assert guard.get_strategy_allocation_cap("HourlyFundingFade-999") == 0.10
    assert guard.get_strategy_allocation_cap("UnseenStrat") == 0.25


def test_dynamic_allocation_enforcement_in_can_open_position():
    guard = PortfolioGuard(max_strategy_equity_pct=0.25)
    guard.update_equity(10_000.0)  # Baseline 25% = $2,500
    inst_id = InstrumentId(Symbol("SOL-USD-PERP"), Venue("HYPERLIQUID"))

    # Initial state: $3,000 proposed exceeds baseline $2,500
    guard._pending_approvals = 0
    can_open, reason = guard.can_open_position(
        strategy_name="TrendContinuationSMC",
        instrument_id=inst_id,
        side=OrderSide.BUY,
        proposed_notional_usd=3000.0,
        current_open_positions_count=0,
    )
    assert can_open is False
    assert "exceeds 25% allocation limit" in reason

    # Scale TrendContinuationSMC up to 40% ($4,000 max)
    winning_trades = [
        {"strategy": "TrendContinuationSMC", "gross_pnl": 50.0, "fees": 1.0, "net_pnl": 49.0},
        {"strategy": "TrendContinuationSMC", "gross_pnl": 50.0, "fees": 1.0, "net_pnl": 49.0},
    ]
    guard.update_dynamic_allocations(winning_trades)
    assert guard.get_strategy_allocation_cap("TrendContinuationSMC") == 0.40

    # Now $3,000 proposed is allowed under 40% cap ($4,000)
    guard._pending_approvals = 0
    can_open_scaled, _ = guard.can_open_position(
        strategy_name="TrendContinuationSMC",
        instrument_id=inst_id,
        side=OrderSide.BUY,
        proposed_notional_usd=3000.0,
        current_open_positions_count=0,
    )
    assert can_open_scaled is True

    # Scale HourlyFundingFade down to 10% ($1,000 max)
    losing_trades = [
        {"strategy": "HourlyFundingFade", "gross_pnl": -50.0, "fees": 1.0, "net_pnl": -51.0},
        {"strategy": "HourlyFundingFade", "gross_pnl": -50.0, "fees": 1.0, "net_pnl": -51.0},
    ]
    guard.update_dynamic_allocations(losing_trades)
    assert guard.get_strategy_allocation_cap("HourlyFundingFade") == 0.10

    # Under 10% ($1,000), a $1,500 order should now be blocked even though it's under baseline 25%
    guard._pending_approvals = 0
    can_open_throttled, reason_throttled = guard.can_open_position(
        strategy_name="HourlyFundingFade",
        instrument_id=inst_id,
        side=OrderSide.BUY,
        proposed_notional_usd=1500.0,
        current_open_positions_count=0,
    )
    assert can_open_throttled is False
    assert "exceeds 10% allocation limit" in reason_throttled


def test_unified_engine_analytics_integration(tmp_path):
    engine = UnifiedEngine(paper=True)
    metrics = engine.get_performance_metrics()
    assert isinstance(metrics, dict)
    assert "win_rate_pct" in metrics
    assert "profit_factor" in metrics

    out_md = str(tmp_path / "test_engine_analytics.md")
    report = engine.generate_performance_report(output_file=out_md)
    assert os.path.exists(out_md)
    assert "# Quantitative Performance Analytics Report" in report

    state = engine.get_state()
    assert "performance_metrics" in state
    assert state["performance_metrics"]["total_trades"] == metrics["total_trades"]
