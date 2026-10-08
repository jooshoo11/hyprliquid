"""
Multi-Agent Sentinel Thoughts Stream for Hyperliquid WezTerm Cockpit.
Streams real-time autonomous reasoning from Groq LPU, AI Studio, and Watchdog agents.
"""

from typing import List, Dict, Any
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widget import Widget
from textual.widgets import Button, Static, RichLog


class SentinelView(Widget):
    """
    Live streaming audit log of all autonomous agent decisions and risk audits.
    """

    DEFAULT_CSS = """
    SentinelView {
        height: 1fr;
        layout: vertical;
        padding: 0 1;
    }

    #sentinel-filter-bar {
        height: 3;
        margin-bottom: 1;
        align: left middle;
    }

    .filter-btn {
        margin-right: 1;
        background: #1e293b;
        color: #94a3b8;
    }
    .filter-btn:hover {
        background: #334155;
        color: #f1f5f9;
    }
    .filter-btn.-active {
        background: #0284c7;
        color: #ffffff;
        text-style: bold;
    }

    #sentinel-stream-log {
        height: 1fr;
        border: solid #1e293b;
        background: #080c14;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.log_widget = RichLog(id="sentinel-stream-log", auto_scroll=True, highlight=True, markup=True)
        self.active_filter = "ALL"
        self.all_thoughts: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="sentinel-filter-bar"):
            yield Button("ALL [A]", id="filter-all", classes="filter-btn -active")
            yield Button("GROQ LPU", id="filter-groq", classes="filter-btn")
            yield Button("AI STUDIO", id="filter-studio", classes="filter-btn")
            yield Button("WATCHDOG", id="filter-watchdog", classes="filter-btn")
            yield Button("REGIME", id="filter-regime", classes="filter-btn")
        yield self.log_widget

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "filter-all":
            self.set_filter("ALL")
        elif btn_id == "filter-groq":
            self.set_filter("GROQ")
        elif btn_id == "filter-studio":
            self.set_filter("STUDIO")
        elif btn_id == "filter-watchdog":
            self.set_filter("WATCHDOG")
        elif btn_id == "filter-regime":
            self.set_filter("REGIME")

    def set_filter(self, filter_name: str) -> None:
        self.active_filter = filter_name
        for btn in self.query(".filter-btn"):
            btn.remove_class("-active")
        btn_match = self.query_one(f"#filter-{filter_name.lower()}", Button)
        if btn_match:
            btn_match.add_class("-active")
        self._repopulate_log()

    def update_thoughts(self, thoughts: List[Dict[str, Any]]) -> None:
        """Receive fresh thoughts stream."""
        # Detect new thoughts and append
        if thoughts != self.all_thoughts:
            self.all_thoughts = thoughts
            self._repopulate_log()

    def _repopulate_log(self) -> None:
        self.log_widget.clear()
        for t in self.all_thoughts:
            cat = t.get("category", "")
            if self.active_filter != "ALL":
                if self.active_filter == "GROQ" and "GROQ" not in cat:
                    continue
                elif self.active_filter == "STUDIO" and "STUDIO" not in cat:
                    continue
                elif self.active_filter == "WATCHDOG" and "WATCHDOG" not in cat:
                    continue
                elif self.active_filter == "REGIME" and "REGIME" not in cat:
                    continue

            ts = t.get("timestamp", "")
            badge_col = t.get("badge_color", "cyan")
            thought = t.get("thought", "")

            color_tag = "[#10b981]" if badge_col == "green" else ("[#f43f5e]" if badge_col == "red" else ("[#f59e0b]" if badge_col == "yellow" else "[#38bdf8]"))
            
            line = f"[dim]{ts}[/dim] {color_tag}{cat}[/] {thought}"
            self.log_widget.write(line)
