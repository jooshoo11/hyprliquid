"""
Unit tests for backtest engine and performance metrics calculator using Polars.
"""

import polars as pl
from src.backtest.run_backtest import compute_performance_metrics, _parse_money_val


def test_parse_money_val():
    assert _parse_money_val(100) == 100.0
    assert _parse_money_val(50.25) == 50.25
    assert _parse_money_val("61.87 USD") == 61.87
    assert _parse_money_val("-15.40 USD") == -15.40
    assert _parse_money_val("10,000.50 USD") == 10000.50
    assert _parse_money_val(None) == 0.0


def test_compute_performance_metrics():
    initial_cap = 10000.0
    account_df = pl.DataFrame({
        "total": ["10000.00 USD", "10150.00 USD", "10100.00 USD", "10250.00 USD"]
    })

    positions_df = pl.DataFrame({
        "realized_pnl": ["150.00 USD", "-50.00 USD", "150.00 USD"]
    })

    metrics = compute_performance_metrics(
        initial_capital=initial_cap,
        account_report=account_df,
        fills_report=None,
        positions_report=positions_df,
    )

    assert metrics["initial_capital"] == 10000.0
    assert metrics["final_equity"] == 10250.0
    assert metrics["net_profit"] == 250.0
    assert metrics["return_pct"] == 2.5
    assert metrics["total_trades"] == 3
    assert metrics["winning_trades"] == 2
    assert metrics["losing_trades"] == 1
    assert round(metrics["win_rate"], 1) == 66.7
    assert metrics["profit_factor"] == 6.0  # 300 / 50
