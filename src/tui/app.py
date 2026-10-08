"""
Hyperliquid AI Cockpit - Master WezTerm Terminal Dashboard Application (app.py)
Institutional multi-tab trading terminal providing real-time portfolio telemetry,
AI Sentinel surveillance, L2 depth microstructure ladders, and 1-click execution.
"""

import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import TabbedContent, TabPane, Footer, Button
from textual.containers import Vertical

from src.tui.data_provider import DataProvider, get_active_engine, set_active_engine
from src.tui.widgets.header_bar import HeaderBar
from src.tui.widgets.positions_view import PositionsView
from src.tui.widgets.prospects_view import ProspectsView
from src.tui.widgets.sentinel_view import SentinelView
from src.tui.widgets.analytics_view import AnalyticsView
from src.tui.widgets.orderbook_view import OrderbookView
from src.tui.widgets.modals import (
    NewTradeModal,
    FlattenAllModal,
    ClosePositionModal,
    ResetBreakerModal,
)


class HyperliquidCockpitApp(App):
    """
    Master WezTerm / Terminal Trading Cockpit Application.
    """

    TITLE = "HYPERLIQUID AI COCKPIT"
    SUB_TITLE = "WezTerm Autonomous Trading Terminal"

    CSS = """
    Screen {
        background: #080c14;
        color: #f1f5f9;
    }

    TabbedContent {
        height: 1fr;
    }

    TabPane {
        padding: 0;
    }

    Tabs {
        background: #0b1120;
        border-bottom: solid #1e293b;
    }

    Tab {
        color: #64748b;
        text-style: bold;
    }

    Tab.-active {
        color: #38bdf8;
        background: #0f172a;
    }

    Footer {
        background: #0b1120;
        color: #94a3b8;
        border-top: solid #1e293b;
    }

    Toast {
        background: #0f172a;
        color: #38bdf8;
        border: solid #0284c7;
    }
    """

    BINDINGS = [
        Binding("1", "tab_positions", "Positions", show=True),
        Binding("2", "tab_prospects", "AI Radar", show=True),
        Binding("3", "tab_sentinel", "Sentinel", show=True),
        Binding("4", "tab_analytics", "Analytics", show=True),
        Binding("5", "tab_orderbook", "L2 Depth", show=True),
        Binding("n", "new_trade", "New Trade", show=True),
        Binding("c", "close_position", "Close Pos", show=True),
        Binding("f", "flatten_all", "FLATTEN ALL", show=True),
        Binding("r", "reset_breaker", "Reset Guard", show=True),
        Binding("s", "trigger_scan", "Scan", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, data_provider: Optional[DataProvider] = None, **kwargs):
        super().__init__(**kwargs)
        self.provider = data_provider or DataProvider()
        self.header_bar = HeaderBar()
        self.positions_view = PositionsView()
        self.prospects_view = ProspectsView()
        self.sentinel_view = SentinelView()
        self.analytics_view = AnalyticsView()
        self.orderbook_view = OrderbookView()

    def compose(self) -> ComposeResult:
        yield self.header_bar
        with TabbedContent(initial="tab-positions", id="tabs-container"):
            with TabPane("[1] Active Positions", id="tab-positions"):
                yield self.positions_view
            with TabPane("[2] AI Radar", id="tab-prospects"):
                yield self.prospects_view
            with TabPane("[3] Sentinel Stream", id="tab-sentinel"):
                yield self.sentinel_view
            with TabPane("[4] Performance & P&L", id="tab-analytics"):
                yield self.analytics_view
            with TabPane("[5] L2 Depth & Orderbook", id="tab-orderbook"):
                yield self.orderbook_view
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_telemetry()
        self.set_interval(1.5, self.refresh_telemetry)

    def refresh_telemetry(self) -> None:
        """Periodic telemetry sync."""
        try:
            state = self.provider.get_state()
            self.header_bar.update_telemetry(state)
            self.positions_view.update_positions(state.get("positions", []))
            self.prospects_view.update_prospects(state.get("prospects", []))
            self.sentinel_view.update_thoughts(state.get("thoughts", []))

            # Update analytics periodically
            metrics = self.provider.get_performance_metrics()
            daily_md = self.provider.get_daily_pnl_markdown()
            journal = self.provider.get_missed_opportunities(limit=30)
            self.analytics_view.update_analytics(metrics, daily_md, journal)

            # Update L2 depth for active coin
            active_coin = self.orderbook_view.active_coin
            depth = self.provider.get_l2_depth(active_coin)
            self.orderbook_view.update_depth(depth)
        except Exception as e:
            pass

    # Navigation actions
    def action_tab_positions(self) -> None:
        self.query_one(TabbedContent).active = "tab-positions"

    def action_tab_prospects(self) -> None:
        self.query_one(TabbedContent).active = "tab-prospects"

    def action_tab_sentinel(self) -> None:
        self.query_one(TabbedContent).active = "tab-sentinel"

    def action_tab_analytics(self) -> None:
        self.query_one(TabbedContent).active = "tab-analytics"

    def action_tab_orderbook(self) -> None:
        self.query_one(TabbedContent).active = "tab-orderbook"

    # Execution actions
    def action_new_trade(self) -> None:
        """Open manual order entry modal."""
        def handle_order(order_data: Optional[Dict[str, Any]]) -> None:
            if order_data:
                coin = order_data["coin"]
                side = order_data["side"]
                ntl = order_data["notional_usd"]
                sl = order_data["stop_loss"]
                tp = order_data["take_profit"]
                strat = order_data["strategy"]
                success = self.provider.execute_trade(coin, side, ntl, sl, tp, strat)
                if success:
                    self.notify(f"🚀 Order submitted: {side} {coin} (${ntl:.2f})", title="Order Placed", severity="information")
                else:
                    self.notify(f"Failed to submit order for {coin}", title="Order Error", severity="error")
                self.refresh_telemetry()

        self.push_screen(NewTradeModal(), handle_order)

    def action_close_position(self) -> None:
        """Close currently selected open position."""
        coin = self.positions_view.get_selected_coin()
        if not coin:
            self.notify("No active position selected to close.", title="Info", severity="warning")
            return

        def handle_close(confirmed: bool) -> None:
            if confirmed:
                success = self.provider.close_position(coin, reason="Terminal User Close")
                if success:
                    self.notify(f"Closed position for {coin}", title="Position Closed", severity="information")
                else:
                    self.notify(f"Failed to close position for {coin}", title="Close Failed", severity="error")
                self.refresh_telemetry()

        self.push_screen(ClosePositionModal(coin), handle_close)

    def action_flatten_all(self) -> None:
        """Prompt and execute emergency flatten."""
        def handle_flatten(confirmed: bool) -> None:
            if confirmed:
                self.notify("🚨 EMERGENCY FLATTEN INITIATED", title="Risk Guardian", severity="warning")
                self.provider.close_all_positions(reason="Emergency Terminal Flatten")
                self.refresh_telemetry()

        self.push_screen(FlattenAllModal(), handle_flatten)

    def action_reset_breaker(self) -> None:
        """Reset circuit breaker modal."""
        def handle_reset(confirmed: bool) -> None:
            if confirmed:
                self.provider.reset_circuit_breaker()
                self.notify("🛡️ Circuit breaker manually reset.", title="Risk Guard", severity="information")
                self.refresh_telemetry()

        self.push_screen(ResetBreakerModal(), handle_reset)

    def action_trigger_scan(self) -> None:
        """Trigger immediate on-demand market prospector scan."""
        self.provider.trigger_prospector_scan()
        self.notify("🔍 Market scan triggered across universe", title="AI Prospector", severity="information")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Delegate button events from subviews."""
        btn_id = event.button.id
        if btn_id == "btn-close-selected":
            self.action_close_position()
        elif btn_id == "btn-flatten":
            self.action_flatten_all()
        elif btn_id == "btn-new-trade":
            self.action_new_trade()
        elif btn_id == "btn-reset-guard":
            self.action_reset_breaker()
        elif btn_id == "btn-force-scan":
            self.action_trigger_scan()
        elif btn_id == "btn-trade-prospect":
            selected = self.prospects_view.get_selected_prospect()
            init_coin = selected.get("coin", "SOL") if selected else "SOL"
            init_bias = selected.get("bias", "LONG") if selected else "LONG"
            self.push_screen(NewTradeModal(initial_coin=init_coin, initial_bias=init_bias), lambda d: self._handle_prospect_submit(d))
        elif btn_id == "btn-ob-refresh":
            depth = self.provider.get_l2_depth(self.orderbook_view.active_coin)
            self.orderbook_view.update_depth(depth)

    def _handle_prospect_submit(self, order_data: Optional[Dict[str, Any]]) -> None:
        if order_data:
            coin = order_data["coin"]
            side = order_data["side"]
            ntl = order_data["notional_usd"]
            sl = order_data["stop_loss"]
            tp = order_data["take_profit"]
            strat = order_data["strategy"]
            self.provider.execute_trade(coin, side, ntl, sl, tp, strat)
            self.notify(f"🚀 Executed Prospect Trade: {side} {coin}", title="AI Execution", severity="information")
            self.refresh_telemetry()


def main():
    app = HyperliquidCockpitApp()
    app.run()


if __name__ == "__main__":
    main()
