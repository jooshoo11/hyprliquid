"""
Header and Telemetry Bar for Hyperliquid WezTerm Cockpit.
Renders real-time system health, circuit guard status, and key financial telemetry.
"""

from typing import Dict, Any
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static

from src.tui.data_provider import build_sparkline


class HeaderBar(Widget):
    """
    Top-level executive telemetry cockpit bar.
    """

    DEFAULT_CSS = """
    HeaderBar {
        height: auto;
        dock: top;
        background: #080c14;
        border-bottom: solid #1e293b;
        padding: 0 1;
    }

    #top-row {
        height: 1;
        margin-top: 0;
        margin-bottom: 0;
    }

    #metrics-row {
        height: 1;
        margin-top: 0;
        margin-bottom: 0;
    }

    .header-logo {
        width: 32;
        text-style: bold;
    }

    .header-subsystems {
        width: 1fr;
        text-align: right;
    }

    .metric-pill {
        margin-right: 2;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logo_widget = Static(id="header-logo", classes="header-logo")
        self.subsystems_widget = Static(id="header-subsystems", classes="header-subsystems")
        self.metrics_widget = Static(id="header-metrics")

    def compose(self) -> ComposeResult:
        with Horizontal(id="top-row"):
            yield self.logo_widget
            yield self.subsystems_widget
        with Horizontal(id="metrics-row"):
            yield self.metrics_widget

    def update_telemetry(self, state: Dict[str, Any]) -> None:
        """Update telemetry metrics display."""
        mode = state.get("mode", "PAPER")
        mode_color = "yellow" if mode == "PAPER" else "red"
        guard = state.get("circuit_guard", {})
        guard_tripped = guard.get("tripped", False)
        guard_text = "🚨 TRIPPED" if guard_tripped else "ACTIVE (Max 20%)"
        guard_color = "red" if guard_tripped else "green"

        sub = state.get("subsystems", {})
        node_status = sub.get("node", "ACTIVE")
        feed_status = sub.get("feed", "50 PAIRS")
        groq_status = sub.get("groq", "ONLINE")

        # Top row: Logo, Mode, Guard & Subsystems
        logo_text = Text()
        logo_text.append("HYPER", style="bold white")
        logo_text.append("LIQUID ", style="bold #38bdf8")
        logo_text.append("[AI SENTINEL]", style="bold #a855f7")
        logo_text.append(f"  [{mode}]", style=f"bold {mode_color}")
        self.logo_widget.update(logo_text)

        sub_text = Text()
        sub_text.append("GUARD: ", style="dim")
        sub_text.append(f"{guard_text}  ", style=f"bold {guard_color}")
        sub_text.append("CORE: ", style="dim")
        sub_text.append(f"{node_status}  ", style="bold green")
        sub_text.append("L2: ", style="dim")
        sub_text.append(f"{feed_status}  ", style="bold green")
        sub_text.append("⚡ GROQ: ", style="dim")
        sub_text.append(f"{groq_status}", style="bold #38bdf8")
        self.subsystems_widget.update(sub_text)

        # Bottom row: Live financial metrics
        equity = state.get("equity", 10000.0)
        cash = state.get("cash", 10000.0)
        margin_used = state.get("margin_used", 0.0)
        margin_pct = (margin_used / equity * 100) if equity > 0 else 0.0
        unrealized = state.get("unrealized_pnl", 0.0)
        realized = state.get("realized_pnl", 0.0)
        roi_total = state.get("roi_total", 0.0)
        fees = state.get("fees", 0.0)
        carry = state.get("carry", 0.0)
        win_rate = state.get("win_rate", 0.0)
        total_trades = state.get("total_trades", 0)
        hist = state.get("equity_history", [])
        sparkline = build_sparkline(hist, width=16)

        unr_color = "green" if unrealized >= 0 else "red"
        unr_sign = "+" if unrealized >= 0 else ""
        real_sign = "+" if realized >= 0 else ""

        met_text = Text()
        met_text.append("EQUITY: ", style="dim")
        met_text.append(f"${equity:,.2f}  ", style="bold white")

        met_text.append("CASH: ", style="dim")
        met_text.append(f"${cash:,.2f}  ", style="white")

        met_text.append("MARGIN: ", style="dim")
        met_text.append(f"${margin_used:,.2f} ({margin_pct:.1f}%)  ", style="white")

        met_text.append("UNR PNL: ", style="dim")
        met_text.append(f"{unr_sign}${unrealized:,.2f} ({unr_sign}{roi_total:.2f}%)  ", style=f"bold {unr_color}")

        met_text.append("REALIZED: ", style="dim")
        met_text.append(f"{real_sign}${realized:,.2f}  ", style="bold green" if realized >= 0 else "bold red")

        met_text.append("FEES/CARRY: ", style="dim")
        met_text.append(f"-${fees:.2f}/+${carry:.2f}  ", style="dim white")

        met_text.append("WIN RATE: ", style="dim")
        met_text.append(f"{win_rate:.1f}% ({total_trades}T)  ", style="bold cyan")

        met_text.append(f"{sparkline}", style="bold #38bdf8")

        self.metrics_widget.update(met_text)
