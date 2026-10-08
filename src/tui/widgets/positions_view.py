"""
Positions View & Execution Controls for Hyperliquid WezTerm Cockpit.
Provides real-time interactive DataTable of open positions and 1-click execution actions.
"""

from typing import List, Dict, Any, Optional
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widget import Widget
from textual.widgets import DataTable, Button, Static


class PositionsView(Widget):
    """
    Active positions table with real-time mark-to-market and emergency flatten controls.
    """

    DEFAULT_CSS = """
    PositionsView {
        height: 1fr;
        layout: vertical;
        padding: 0 1;
    }

    #positions-summary-bar {
        height: 1;
        margin-bottom: 1;
        color: #94a3b8;
    }

    #positions-table {
        height: 1fr;
        border: solid #1e293b;
        background: #080c14;
    }

    #positions-actions-bar {
        height: 3;
        margin-top: 1;
        align: left middle;
    }

    .pos-btn {
        margin-right: 1;
    }

    #btn-flatten {
        background: #b91c1c;
        color: #ffffff;
        text-style: bold;
    }
    #btn-flatten:hover {
        background: #dc2626;
    }

    #btn-close-selected {
        background: #1e293b;
        color: #f1f5f9;
    }
    #btn-close-selected:hover {
        background: #334155;
    }

    #btn-new-trade {
        background: #0284c7;
        color: #ffffff;
        text-style: bold;
    }
    #btn-new-trade:hover {
        background: #0369a1;
    }

    #btn-reset-guard {
        background: #78350f;
        color: #fef3c7;
    }
    #btn-reset-guard:hover {
        background: #92400e;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.summary_bar = Static(id="positions-summary-bar")
        self.table = DataTable(id="positions-table", cursor_type="row")
        self.current_positions: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield self.summary_bar
        yield self.table
        with Horizontal(id="positions-actions-bar"):
            yield Button("✕ Close Pos [C]", id="btn-close-selected", classes="pos-btn")
            yield Button("🚨 FLATTEN ALL [F]", id="btn-flatten", classes="pos-btn")
            yield Button("+ New Trade [N]", id="btn-new-trade", classes="pos-btn")
            yield Button("🛡️ Reset Guard [R]", id="btn-reset-guard", classes="pos-btn")

    def on_mount(self) -> None:
        self.table.add_column("Coin", width=10)
        self.table.add_column("Side", width=8)
        self.table.add_column("Size", width=12)
        self.table.add_column("Entry Px", width=12)
        self.table.add_column("Mark Px", width=12)
        self.table.add_column("Unrealized PnL", width=16)
        self.table.add_column("ROI %", width=10)
        self.table.add_column("Liq Px", width=12)
        self.table.add_column("Strategy", width=18)

    def update_positions(self, positions: List[Dict[str, Any]]) -> None:
        """Refresh table rows from new positions list."""
        self.current_positions = positions
        self.table.clear()

        total_notional = sum(float(p.get("entry_price", 0)) * float(p.get("size", 0)) for p in positions)
        total_pnl = sum(float(p.get("unrealized_pnl", 0)) for p in positions)
        pnl_color = "green" if total_pnl >= 0 else "red"

        summary = Text()
        summary.append(f"ACTIVE POSITIONS ({len(positions)}/3)  ", style="bold white")
        summary.append(f"Total Exposure: ${total_notional:,.2f}  ", style="dim white")
        summary.append(f"Net Unrealized: ", style="dim")
        summary.append(f"${total_pnl:+,.2f}  ", style=f"bold {pnl_color}")
        summary.append("[Press C to close selected | F to emergency flatten]", style="dim italic")
        self.summary_bar.update(summary)

        if not positions:
            self.table.add_row("---", "---", "---", "---", "---", "No Open Trades", "---", "---", "System Flat")
            return

        for p in positions:
            coin = p.get("coin", "UNKNOWN")
            side = p.get("side", "LONG").upper()
            side_color = "green" if side == "LONG" else "red"

            size = float(p.get("size", 0.0))
            entry_px = float(p.get("entry_price", 0.0))
            mark_px = float(p.get("mark_price", entry_px))
            pnl = float(p.get("unrealized_pnl", 0.0))
            roi = float(p.get("roi_pct", 0.0))
            liq_px = float(p.get("liq_price", 0.0)) if p.get("liq_price") else 0.0
            strat = p.get("strategy", "SMC Trend")

            pnl_style = "bold green" if pnl >= 0 else "bold red"
            roi_style = "green" if roi >= 0 else "red"

            self.table.add_row(
                Text(coin, style="bold white"),
                Text(side, style=f"bold {side_color}"),
                Text(f"{size:,.4f}", style="white"),
                Text(f"${entry_px:,.4f}" if entry_px < 10 else f"${entry_px:,.2f}", style="dim white"),
                Text(f"${mark_px:,.4f}" if mark_px < 10 else f"${mark_px:,.2f}", style="white"),
                Text(f"${pnl:+,.2f}", style=pnl_style),
                Text(f"{roi:+.2f}%", style=roi_style),
                Text(f"${liq_px:,.2f}" if liq_px > 0 else "--", style="dim yellow"),
                Text(strat, style="cyan"),
                key=coin,
            )

    def get_selected_coin(self) -> Optional[str]:
        """Return coin key of selected table row."""
        try:
            cell_key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate)
            row_key = cell_key.row_key.value
            if row_key and row_key != "---":
                return row_key
        except Exception:
            pass
        return None
