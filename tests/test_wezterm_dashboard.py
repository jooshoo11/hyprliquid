"""
Unit and integration tests for Hyperliquid WezTerm Cockpit and Terminal TUI.
Validates telemetry provider, orderbook parsing, modal operations, and Textual app lifecycle.
"""

import os
import sys
import json
import subprocess
import pytest
from unittest.mock import MagicMock

from src.tui.data_provider import DataProvider, build_sparkline, set_active_engine, get_active_engine
from src.tui.app import HyperliquidCockpitApp
from src.tui.widgets.modals import (
    NewTradeModal,
    FlattenAllModal,
    ClosePositionModal,
    ResetBreakerModal,
)


def test_sparkline_builder():
    """Verify sparkline generator handles uniform and varying series."""
    empty = build_sparkline([])
    assert len(empty) == 20

    flat = build_sparkline([100.0] * 10)
    assert len(flat) == 10

    rising = build_sparkline([10, 20, 30, 40, 50, 60, 70, 80])
    assert rising[-1] == "█"
    assert rising[0] in (" ", " ")


def test_data_provider_bridge_telemetry(tmp_path):
    """Test DataProvider loads from bridge JSON files when running detached."""
    bridge_dir = tmp_path / "bridge"
    reports_dir = tmp_path / "reports"
    bridge_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    paper_file = bridge_dir / "paper_state.json"
    paper_file.write_text(json.dumps({
        "equity": 10500.0,
        "cash_balance": 10200.0,
        "realized_pnl": 300.0,
    }))

    trades_file = bridge_dir / "active_trades.json"
    trades_file.write_text(json.dumps([
        {
            "coin": "SOL",
            "side": "LONG",
            "size": 2.5,
            "entry_price": 140.0,
            "mark_price": 145.0,
            "unrealized_pnl": 12.5,
            "roi_pct": 3.57,
            "strategy": "SMC Trend",
        }
    ]))

    prospects_file = bridge_dir / "prospects.json"
    prospects_file.write_text(json.dumps({
        "prospects": [
            {
                "coin": "BTC",
                "bias": "BULLISH",
                "conviction_score": 92,
                "target_entry": 65000.0,
                "stop_loss": 64000.0,
                "take_profit": 68000.0,
                "catalyst_verdict": "ETF Inflow momentum",
                "rationale": "High orderbook absorption",
            }
        ]
    }))

    groq_file = bridge_dir / "groq_sentinel.json"
    groq_file.write_text(json.dumps({
        "status": "HEALTHY",
        "action": "NONE",
        "reason": "Liquidity walls intact",
        "_latency_ms": 105.0,
    }))

    # Reset any active engine
    set_active_engine(None)

    dp = DataProvider(repo_root=str(tmp_path))
    state = dp.get_state()

    assert state["mode"] == "PAPER"
    assert state["equity"] >= 10200.0
    assert len(state["positions"]) == 1
    assert state["positions"][0]["coin"] == "SOL"
    assert len(state["prospects"]) == 1
    assert state["prospects"][0]["coin"] == "BTC"
    assert len(state["thoughts"]) >= 1
    assert state["circuit_guard"]["tripped"] is False


def test_data_provider_in_memory_engine():
    """Test DataProvider dispatches directly to in-memory UnifiedEngine."""
    mock_engine = MagicMock()
    mock_engine.paper = True
    mock_engine.get_state.return_value = {
        "equity": 11000.0,
        "cash_balance": 10800.0,
        "net_unrealized": 200.0,
        "net_realized_pnl": 500.0,
        "roi_total": 1.85,
        "positions": [
            {
                "coin": "ETH",
                "side": "SHORT",
                "size": 1.0,
                "entry_price": 2600.0,
                "mark_price": 2550.0,
                "unrealized_pnl": 50.0,
                "roi_pct": 1.92,
                "strategy": "Funding Fade",
            }
        ],
    }
    mock_engine.close_position.return_value = True
    mock_engine.close_all_positions.return_value = ["ETH"]
    mock_engine.execute_prospect_entry.return_value = True

    set_active_engine(mock_engine)
    dp = DataProvider()

    state = dp.get_state()
    assert state["equity"] == 11000.0
    assert state["positions"][0]["coin"] == "ETH"

    # Action execution
    assert dp.close_position("ETH", reason="Test Close") is True
    mock_engine.close_position.assert_called_with("ETH", reason="Test Close")

    assert dp.close_all_positions(reason="Test Flatten") is True
    mock_engine.close_all_positions.assert_called_with(reason="Test Flatten")

    assert dp.execute_trade("SOL", "LONG", 50.0) is True
    assert mock_engine.execute_prospect_entry.called

    assert dp.trigger_prospector_scan() is True
    assert mock_engine._force_prospector_scan is True

    # Reset
    set_active_engine(None)


def test_l2_depth_calculation():
    """Test L2 orderbook parsing and imbalance calculation."""
    dp = DataProvider()
    dp.info_client = MagicMock()
    dp.info_client.get_l2_snapshot.return_value = {
        "status": "SUCCESS",
        "coin": "BTC",
        "bids": [{"px": "65000", "sz": "2.0"}, {"px": "64990", "sz": "3.0"}],
        "asks": [{"px": "65010", "sz": "1.0"}, {"px": "65020", "sz": "1.5"}],
    }

    depth = dp.get_l2_depth("BTC")
    assert depth["coin"] == "BTC"
    assert depth["top_bid"] == 65000.0
    assert depth["top_ask"] == 65010.0
    assert depth["spread"] == 10.0
    assert depth["bid_vol"] == 5.0
    assert depth["ask_vol"] == 2.5
    assert depth["imbalance"] == 2.0  # 5.0 / 2.5 = 2.0


def test_wezterm_lua_syntax():
    """Ensure wezterm.lua parses correctly with wezterm CLI."""
    wezterm_bin = "/home/jooshoo/.local/bin/wezterm"
    if os.path.exists(wezterm_bin):
        res = subprocess.run([wezterm_bin, "--config-file", "wezterm.lua", "ls-fonts"], capture_output=True, text=True)
        assert res.returncode == 0, f"WezTerm config error: {res.stderr}"


@pytest.mark.asyncio
async def test_tui_app_lifecycle():
    """Test Textual Cockpit App mounts, updates telemetry, and switches tabs."""
    mock_dp = MagicMock()
    mock_dp.get_state.return_value = {
        "mode": "PAPER",
        "equity": 10000.0,
        "cash": 10000.0,
        "margin_used": 0.0,
        "margin_available": 10000.0,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "roi_total": 0.0,
        "fees": 0.0,
        "carry": 0.0,
        "win_rate": 60.0,
        "total_trades": 10,
        "winning_trades": 6,
        "circuit_guard": {"status": "ACTIVE", "tripped": False, "current_dd_pct": 0.0},
        "subsystems": {"node": "ACTIVE", "feed": "50 PAIRS", "groq": "ONLINE (<115ms)"},
        "positions": [],
        "prospects": [],
        "thoughts": [],
        "equity_history": [10000.0],
    }
    mock_dp.get_performance_metrics.return_value = {
        "sharpe_ratio": 2.1,
        "profit_factor": 1.8,
        "win_rate_pct": 60.0,
        "expectancy_usd": 12.5,
        "total_trades": 10,
        "max_drawdown_pct": 2.5,
        "total_net_pnl": 250.0,
        "avg_win_usd": 50.0,
        "avg_loss_usd": 25.0,
    }
    mock_dp.get_daily_pnl_markdown.return_value = "# Daily P&L\nEverything normal."
    mock_dp.get_missed_opportunities.return_value = []
    mock_dp.get_l2_depth.return_value = {
        "coin": "BTC",
        "top_bid": 65000.0,
        "top_ask": 65010.0,
        "spread": 10.0,
        "spread_bps": 1.5,
        "bids": [],
        "asks": [],
        "bid_vol": 0.0,
        "ask_vol": 0.0,
        "imbalance": 1.0,
    }

    app = HyperliquidCockpitApp(data_provider=mock_dp)
    async with app.run_test() as pilot:
        # Check header mounted
        assert app.header_bar is not None
        assert app.positions_view is not None
        assert app.prospects_view is not None

        # Test tab switching via keyboard bindings
        await pilot.press("2")
        assert app.query_one("#tabs-container").active == "tab-prospects"

        await pilot.press("3")
        assert app.query_one("#tabs-container").active == "tab-sentinel"

        await pilot.press("4")
        assert app.query_one("#tabs-container").active == "tab-analytics"

        await pilot.press("5")
        assert app.query_one("#tabs-container").active == "tab-orderbook"

        await pilot.press("1")
        assert app.query_one("#tabs-container").active == "tab-positions"


def test_modals_construction():
    """Ensure all modal screens construct without errors."""
    m_new = NewTradeModal(initial_coin="SOL", initial_bias="LONG")
    assert m_new.initial_coin == "SOL"
    assert m_new.selected_side == "LONG"

    m_flatten = FlattenAllModal()
    assert m_flatten is not None

    m_close = ClosePositionModal(coin="BTC")
    assert m_close.coin == "BTC"

    m_reset = ResetBreakerModal()
    assert m_reset is not None
