"""
Interactive Modals and Dialog Screens for Hyperliquid WezTerm Cockpit.
Provides modal confirmation dialogs for Emergency Flatten, New Trade Entry, and Circuit Breaker Reset.
"""

from typing import Optional, Dict, Any
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal, Grid
from textual.widgets import Static, Button, Input, Label


class FlattenAllModal(ModalScreen[bool]):
    """
    Emergency Flatten All confirmation dialog.
    """

    DEFAULT_CSS = """
    FlattenAllModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #flatten-dialog {
        width: 60;
        height: auto;
        background: #0f172a;
        border: thick #dc2626;
        padding: 1 2;
    }

    #flatten-title {
        color: #ef4444;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }

    #flatten-message {
        text-align: center;
        margin-bottom: 1;
        color: #f8fafc;
    }

    #flatten-buttons {
        height: 3;
        align: center middle;
    }

    #btn-confirm-flatten {
        background: #dc2626;
        color: #ffffff;
        text-style: bold;
        margin-right: 2;
    }

    #btn-cancel-flatten {
        background: #334155;
        color: #f1f5f9;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="flatten-dialog"):
            yield Static("🚨 EMERGENCY FLATTEN ALL POSITIONS", id="flatten-title")
            yield Static(
                "Are you sure you want to emergency close ALL active positions\n"
                "at market price and cancel pending orders?\n\n"
                "This action is immediate and non-reversible.",
                id="flatten-message",
            )
            with Horizontal(id="flatten-buttons"):
                yield Button("CONFIRM FLATTEN NOW", id="btn-confirm-flatten")
                yield Button("Cancel [Esc]", id="btn-cancel-flatten")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-flatten":
            self.dismiss(True)
        else:
            self.dismiss(False)


class ClosePositionModal(ModalScreen[bool]):
    """
    Confirm closing individual coin position.
    """

    DEFAULT_CSS = """
    ClosePositionModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #close-dialog {
        width: 50;
        height: auto;
        background: #0f172a;
        border: solid #0284c7;
        padding: 1 2;
    }

    #close-title {
        color: #38bdf8;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }

    #close-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(self, coin: str, **kwargs):
        super().__init__(**kwargs)
        self.coin = coin

    def compose(self) -> ComposeResult:
        with Vertical(id="close-dialog"):
            yield Static(f"CLOSE POSITION: {self.coin}", id="close-title")
            yield Static(f"Execute market close order for active {self.coin} position?")
            with Horizontal(id="close-buttons"):
                yield Button("Confirm Close", id="btn-confirm-close", classes="-primary")
                yield Button("Cancel", id="btn-cancel-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-close":
            self.dismiss(True)
        else:
            self.dismiss(False)


class ResetBreakerModal(ModalScreen[bool]):
    """
    Reset Circuit Breaker confirmation dialog.
    """

    DEFAULT_CSS = """
    ResetBreakerModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #reset-dialog {
        width: 55;
        height: auto;
        background: #0f172a;
        border: solid #f59e0b;
        padding: 1 2;
    }

    #reset-title {
        color: #fbbf24;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }

    #reset-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="reset-dialog"):
            yield Static("RESET CIRCUIT BREAKER", id="reset-title")
            yield Static("Restart PortfolioGuard daily drawdown window and re-enable new order execution?")
            with Horizontal(id="reset-buttons"):
                yield Button("Reset Breaker", id="btn-confirm-reset", classes="-warning")
                yield Button("Cancel", id="btn-cancel-reset")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-reset":
            self.dismiss(True)
        else:
            self.dismiss(False)


class NewTradeModal(ModalScreen[Optional[Dict[str, Any]]]):
    """
    Interactive Order Entry Dialog for manual executions.
    """

    DEFAULT_CSS = """
    NewTradeModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #trade-dialog {
        width: 60;
        height: auto;
        background: #0f172a;
        border: solid #0284c7;
        padding: 1 2;
    }

    #trade-title {
        color: #38bdf8;
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }

    .form-row {
        height: 3;
        margin-bottom: 1;
        align: left middle;
    }

    .form-label {
        width: 14;
        color: #94a3b8;
    }

    .form-input {
        width: 1fr;
    }

    #side-toggle-container {
        height: 3;
        align: left middle;
        margin-bottom: 1;
    }

    .side-btn {
        margin-right: 1;
        width: 16;
    }

    .side-btn.-selected-long {
        background: #15803d;
        color: #ffffff;
        text-style: bold;
    }

    .side-btn.-selected-short {
        background: #b91c1c;
        color: #ffffff;
        text-style: bold;
    }

    #trade-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    #btn-submit-order {
        background: #0284c7;
        color: #ffffff;
        text-style: bold;
        margin-right: 2;
    }
    """

    def __init__(self, initial_coin: str = "SOL", initial_bias: str = "LONG", **kwargs):
        super().__init__(**kwargs)
        self.initial_coin = initial_coin
        self.selected_side = initial_bias.upper() if initial_bias.upper() in ("LONG", "SHORT") else "LONG"

    def compose(self) -> ComposeResult:
        with Vertical(id="trade-dialog"):
            yield Static("NEW TRADE ORDER ENTRY", id="trade-title")

            with Horizontal(id="side-toggle-container"):
                yield Label("Direction:", classes="form-label")
                yield Button("LONG", id="btn-side-long", classes="side-btn -selected-long" if self.selected_side == "LONG" else "side-btn")
                yield Button("SHORT", id="btn-side-short", classes="side-btn -selected-short" if self.selected_side == "SHORT" else "side-btn")

            with Horizontal(classes="form-row"):
                yield Label("Coin:", classes="form-label")
                yield Input(value=self.initial_coin, placeholder="e.g. SOL, BTC, ETH", id="input-coin", classes="form-input")

            with Horizontal(classes="form-row"):
                yield Label("Notional USD:", classes="form-label")
                yield Input(value="25.0", placeholder="25.0", id="input-notional", classes="form-input")

            with Horizontal(classes="form-row"):
                yield Label("Stop Loss ($):", classes="form-label")
                yield Input(value="", placeholder="Optional (e.g. 142.50)", id="input-sl", classes="form-input")

            with Horizontal(classes="form-row"):
                yield Label("Take Profit ($):", classes="form-label")
                yield Input(value="", placeholder="Optional (e.g. 165.00)", id="input-tp", classes="form-input")

            with Horizontal(id="trade-buttons"):
                yield Button("Submit Order [Enter]", id="btn-submit-order")
                yield Button("Cancel [Esc]", id="btn-cancel-trade")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-side-long":
            self.selected_side = "LONG"
            self.query_one("#btn-side-long").add_class("-selected-long")
            self.query_one("#btn-side-short").remove_class("-selected-short")
        elif btn_id == "btn-side-short":
            self.selected_side = "SHORT"
            self.query_one("#btn-side-short").add_class("-selected-short")
            self.query_one("#btn-side-long").remove_class("-selected-long")
        elif btn_id == "btn-submit-order":
            self._submit()
        elif btn_id == "btn-cancel-trade":
            self.dismiss(None)

    def _submit(self) -> None:
        coin = self.query_one("#input-coin", Input).value.strip().upper()
        if not coin:
            return

        try:
            notional = float(self.query_one("#input-notional", Input).value.strip() or 25.0)
        except ValueError:
            notional = 25.0

        sl_str = self.query_one("#input-sl", Input).value.strip()
        tp_str = self.query_one("#input-tp", Input).value.strip()

        sl = float(sl_str) if sl_str else None
        tp = float(tp_str) if tp_str else None

        self.dismiss({
            "coin": coin,
            "side": self.selected_side,
            "notional_usd": notional,
            "stop_loss": sl,
            "take_profit": tp,
            "strategy": "Manual Cockpit",
        })
