"""
Performance Analytics & Daily P&L View for Hyperliquid WezTerm Cockpit.
Displays institutional risk metrics, historical reports, and missed opportunities retrospective.
"""

from typing import Dict, Any, List
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, Grid
from textual.widget import Widget
from textual.widgets import Static, Markdown, DataTable


class AnalyticsView(Widget):
    """
    Performance telemetry cards, Markdown P&L report, and retrospective journal.
    """

    DEFAULT_CSS = """
    AnalyticsView {
        height: 1fr;
        layout: vertical;
        padding: 0 1;
    }

    #metrics-grid {
        grid-size: 4 2;
        grid-gutter: 1 1;
        height: 8;
        margin-bottom: 1;
    }

    .metric-card {
        background: #0f172a;
        border: solid #1e293b;
        padding: 1;
        content-align: center middle;
    }

    #reports-container {
        height: 1fr;
        layout: horizontal;
    }

    #markdown-box {
        width: 1fr;
        height: 1fr;
        border: solid #1e293b;
        background: #080c14;
        padding: 1;
        overflow-y: scroll;
    }

    #missed-box {
        width: 1fr;
        height: 1fr;
        border: solid #1e293b;
        background: #080c14;
        margin-left: 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.card_sharpe = Static(classes="metric-card", id="card-sharpe")
        self.card_pf = Static(classes="metric-card", id="card-pf")
        self.card_winrate = Static(classes="metric-card", id="card-winrate")
        self.card_expectancy = Static(classes="metric-card", id="card-expectancy")
        self.card_trades = Static(classes="metric-card", id="card-trades")
        self.card_dd = Static(classes="metric-card", id="card-dd")
        self.card_netpnl = Static(classes="metric-card", id="card-netpnl")
        self.card_avgwinloss = Static(classes="metric-card", id="card-avgwinloss")

        self.markdown_report = Markdown(id="markdown-report")
        self.journal_table = DataTable(id="missed-table", cursor_type="row")

    def compose(self) -> ComposeResult:
        with Grid(id="metrics-grid"):
            yield self.card_sharpe
            yield self.card_pf
            yield self.card_winrate
            yield self.card_expectancy
            yield self.card_trades
            yield self.card_dd
            yield self.card_netpnl
            yield self.card_avgwinloss

        with Horizontal(id="reports-container"):
            with Vertical(id="markdown-box"):
                yield self.markdown_report
            with Vertical(id="missed-box"):
                yield Static(Text("RETROSPECTIVE DECISION JOURNAL & MISSED SETUPS", style="bold white"), classes="journal-title")
                yield self.journal_table

    def on_mount(self) -> None:
        self.journal_table.add_column("Timestamp", width=12)
        self.journal_table.add_column("Coin", width=8)
        self.journal_table.add_column("Signal", width=10)
        self.journal_table.add_column("Reason / Filter Gate", width=30)

    def update_analytics(self, metrics: Dict[str, Any], daily_md: str, journal_items: List[Dict[str, Any]]) -> None:
        """Update metrics cards, report viewer, and decision journal."""
        sharpe = metrics.get("sharpe_ratio", 0.0)
        pf = metrics.get("profit_factor", 0.0)
        wr = metrics.get("win_rate_pct", 0.0)
        exp = metrics.get("expectancy_usd", 0.0)
        total = metrics.get("total_trades", 0)
        dd = metrics.get("max_drawdown_pct", 0.0)
        net_pnl = metrics.get("total_net_pnl", 0.0)
        avg_win = metrics.get("avg_win_usd", 0.0)
        avg_loss = metrics.get("avg_loss_usd", 0.0)

        self.card_sharpe.update(f"[dim]SHARPE RATIO[/dim]\n[bold #38bdf8]{sharpe:.2f}[/]")
        self.card_pf.update(f"[dim]PROFIT FACTOR[/dim]\n[bold green]{pf:.2f}[/]")
        self.card_winrate.update(f"[dim]WIN RATE[/dim]\n[bold green]{wr:.1f}%[/]")
        self.card_expectancy.update(f"[dim]EXPECTANCY[/dim]\n[bold #38bdf8]${exp:+.2f}[/]")
        self.card_trades.update(f"[dim]TOTAL TRADES[/dim]\n[bold white]{total}[/]")
        self.card_dd.update(f"[dim]MAX DRAWDOWN[/dim]\n[bold yellow]{dd:.1f}%[/]")
        self.card_netpnl.update(f"[dim]NET PNL[/dim]\n[bold {'green' if net_pnl >= 0 else 'red'}]${net_pnl:+,.2f}[/]")
        self.card_avgwinloss.update(f"[dim]AVG WIN/LOSS[/dim]\n[bold green]+${avg_win:.1f}[/] / [bold red]-${avg_loss:.1f}[/]")

        # Update Markdown
        self.markdown_report.update(daily_md)

        # Update Missed setups
        self.journal_table.clear()
        if not journal_items:
            self.journal_table.add_row("--:--:--", "---", "NONE", "No missed setups recorded")
        else:
            for item in journal_items[-30:]:
                ts = str(item.get("timestamp", ""))[-8:]
                coin = item.get("coin", "UNKNOWN")
                sig = item.get("signal", item.get("bias", "SIGNAL"))
                reason = item.get("filter_reason", item.get("reason", "Filtered by Risk Guard"))
                self.journal_table.add_row(
                    Text(ts, style="dim"),
                    Text(coin, style="bold white"),
                    Text(sig, style="cyan"),
                    Text(reason, style="dim white"),
                )
