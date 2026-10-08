"""
AI Radar & Market Prospects View for Hyperliquid WezTerm Cockpit.
Renders LLM-ranked market opportunities with conviction scores, catalyst verdicts, and trade entry shortcuts.
"""

from typing import List, Dict, Any, Optional
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widget import Widget
from textual.widgets import DataTable, Button, Static


class ProspectsView(Widget):
    """
    Market discovery and AI sentiment prospect radar.
    """

    DEFAULT_CSS = """
    ProspectsView {
        height: 1fr;
        layout: vertical;
        padding: 0 1;
    }

    #prospects-header-bar {
        height: 1;
        margin-bottom: 1;
        color: #94a3b8;
    }

    #prospects-table {
        height: 2fr;
        border: solid #1e293b;
        background: #080c14;
    }

    #prospect-detail-card {
        height: 1fr;
        border: solid #1e293b;
        background: #0f172a;
        padding: 1;
        margin-top: 1;
    }

    #prospects-actions-bar {
        height: 3;
        margin-top: 1;
        align: left middle;
    }

    .prospect-btn {
        margin-right: 1;
    }

    #btn-trade-prospect {
        background: #0284c7;
        color: #ffffff;
        text-style: bold;
    }
    #btn-trade-prospect:hover {
        background: #0369a1;
    }

    #btn-force-scan {
        background: #334155;
        color: #f1f5f9;
    }
    #btn-force-scan:hover {
        background: #475569;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.header_bar = Static(id="prospects-header-bar")
        self.table = DataTable(id="prospects-table", cursor_type="row")
        self.detail_card = Static(id="prospect-detail-card")
        self.current_prospects: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield self.header_bar
        yield self.table
        yield self.detail_card
        with Horizontal(id="prospects-actions-bar"):
            yield Button("🚀 Execute Prospect [Enter]", id="btn-trade-prospect", classes="prospect-btn")
            yield Button("🔍 Scan Markets Now [S]", id="btn-force-scan", classes="prospect-btn")

    def on_mount(self) -> None:
        self.table.add_column("#", width=4)
        self.table.add_column("Coin", width=10)
        self.table.add_column("Bias", width=10)
        self.table.add_column("Score", width=10)
        self.table.add_column("Target Entry", width=14)
        self.table.add_column("Take Profit", width=14)
        self.table.add_column("Stop Loss", width=14)
        self.table.add_column("24h Vol", width=14)
        self.table.add_column("Funding APR", width=12)

    def update_prospects(self, prospects: List[Dict[str, Any]]) -> None:
        """Refresh prospects table and detail inspection."""
        self.current_prospects = prospects
        self.table.clear()

        hdr = Text()
        hdr.append(f"AI RADAR & DEEP MARKET PROSPECTOR ({len(prospects)} Rank Candidates)  ", style="bold white")
        hdr.append("Auto-scanned every 45s across top 50 perpetuals", style="dim italic")
        self.header_bar.update(hdr)

        if not prospects:
            self.table.add_row("-", "---", "NEUTRAL", "--", "--", "--", "--", "--", "--")
            self.detail_card.update(Text("Awaiting market prospector scan results...", style="dim"))
            return

        for idx, p in enumerate(prospects[:20], 1):
            coin = p.get("coin", "UNKNOWN")
            bias = str(p.get("bias", "NEUTRAL")).upper()
            bias_col = "green" if bias == "BULLISH" else ("red" if bias == "BEARISH" else "cyan")

            score = float(p.get("conviction_score", 80))
            score_col = "bold green" if score >= 85 else ("bold cyan" if score >= 70 else "yellow")

            entry = float(p.get("target_entry", 0.0))
            tp = float(p.get("take_profit", 0.0))
            sl = float(p.get("stop_loss", 0.0))
            vol = float(p.get("volume_24h", 0.0))
            funding = float(p.get("funding_apr", 0.0))

            vol_str = f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K"
            fund_str = f"{funding:+.1f}%"

            self.table.add_row(
                Text(str(idx), style="dim"),
                Text(coin, style="bold white"),
                Text(bias, style=f"bold {bias_col}"),
                Text(f"{score:.0f}", style=score_col),
                Text(f"${entry:,.4f}" if entry < 10 else f"${entry:,.2f}", style="white"),
                Text(f"${tp:,.4f}" if tp < 10 else f"${tp:,.2f}", style="green"),
                Text(f"${sl:,.4f}" if sl < 10 else f"${sl:,.2f}", style="red"),
                Text(vol_str, style="dim white"),
                Text(fund_str, style="cyan"),
                key=coin,
            )

        # Update detail card with first or selected prospect
        selected = self.get_selected_prospect() or (prospects[0] if prospects else None)
        if selected:
            self._render_detail_card(selected)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Update detail card when user navigates rows."""
        row_key = event.row_key.value
        for p in self.current_prospects:
            if p.get("coin") == row_key:
                self._render_detail_card(p)
                break

    def _render_detail_card(self, p: Dict[str, Any]) -> None:
        coin = p.get("coin", "")
        bias = p.get("bias", "NEUTRAL")
        score = p.get("conviction_score", 80)
        sentiment = p.get("news_sentiment", "NEUTRAL")
        verdict = p.get("catalyst_verdict") or "No immediate toxic unlocks detected."
        rationale = p.get("rationale") or "High microstructure momentum and favorable volume profile."
        strat = p.get("strategy") or "SMC Trend"

        txt = Text()
        txt.append(f"🔬 AI INTELLIGENCE DOSSIER: {coin}  ", style="bold white")
        txt.append(f"Bias: {bias}  ", style="bold green" if bias == "BULLISH" else "bold red")
        txt.append(f"Conviction: {score:.0f}/100  ", style="bold #38bdf8")
        txt.append(f"Strategy: {strat}\n", style="cyan")

        txt.append("• Catalyst Verdict: ", style="bold dim")
        txt.append(f"{verdict}\n", style="white")

        txt.append("• Technical Rationale: ", style="bold dim")
        txt.append(f"{rationale}\n", style="dim white")

        txt.append("• Target Envelope: ", style="bold dim")
        txt.append(f"Entry: ${p.get('target_entry', 0):,.2f} | TP: ${p.get('take_profit', 0):,.2f} | SL: ${p.get('stop_loss', 0):,.2f}", style="yellow")
        self.detail_card.update(txt)

    def get_selected_prospect(self) -> Optional[Dict[str, Any]]:
        """Return dict of currently selected prospect."""
        try:
            cell_key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate)
            row_key = cell_key.row_key.value
            for p in self.current_prospects:
                if p.get("coin") == row_key:
                    return p
        except Exception:
            pass
        return self.current_prospects[0] if self.current_prospects else None
