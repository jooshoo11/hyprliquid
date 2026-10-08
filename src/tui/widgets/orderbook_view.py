"""
L2 Orderbook Depth & Microstructure Visualizer for Hyperliquid WezTerm Cockpit.
Renders real-time bid/ask depth ladders, volume bars, spread analytics, and imbalance metrics.
"""

from typing import Dict, Any, List
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widget import Widget
from textual.widgets import Static, Input, Button


class OrderbookView(Widget):
    """
    Live L2 Market Depth & Orderbook Visualizer.
    """

    DEFAULT_CSS = """
    OrderbookView {
        height: 1fr;
        layout: vertical;
        padding: 0 1;
    }

    #ob-controls-bar {
        height: 3;
        margin-bottom: 1;
        align: left middle;
    }

    .coin-quick-btn {
        margin-right: 1;
        background: #1e293b;
        color: #94a3b8;
    }
    .coin-quick-btn:hover {
        background: #334155;
        color: #f1f5f9;
    }
    .coin-quick-btn.-active {
        background: #0284c7;
        color: #ffffff;
        text-style: bold;
    }

    #ob-coin-input {
        width: 14;
        margin-right: 1;
    }

    #ob-ladder-container {
        height: 1fr;
        border: solid #1e293b;
        background: #080c14;
        padding: 1 2;
    }

    #ob-stats-bar {
        height: 2;
        margin-top: 1;
        border-top: solid #1e293b;
        color: #94a3b8;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.active_coin = "BTC"
        self.ladder_display = Static(id="ob-ladder-display")
        self.stats_bar = Static(id="ob-stats-bar")

    def compose(self) -> ComposeResult:
        with Horizontal(id="ob-controls-bar"):
            yield Input(value="BTC", placeholder="Coin", id="ob-coin-input")
            yield Button("BTC", id="coin-btc", classes="coin-quick-btn -active")
            yield Button("ETH", id="coin-eth", classes="coin-quick-btn")
            yield Button("SOL", id="coin-sol", classes="coin-quick-btn")
            yield Button("HYPE", id="coin-hype", classes="coin-quick-btn")
            yield Button("Refresh", id="btn-ob-refresh", classes="coin-quick-btn")

        with Vertical(id="ob-ladder-container"):
            yield self.ladder_display
            yield self.stats_bar

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "coin-btc":
            self.select_coin("BTC")
        elif btn_id == "coin-eth":
            self.select_coin("ETH")
        elif btn_id == "coin-sol":
            self.select_coin("SOL")
        elif btn_id == "coin-hype":
            self.select_coin("HYPE")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.select_coin(event.value.strip().upper())

    def select_coin(self, coin: str) -> None:
        self.active_coin = coin.upper()
        try:
            inp = self.query_one("#ob-coin-input", Input)
            inp.value = self.active_coin
        except Exception:
            pass
        for btn in self.query(".coin-quick-btn"):
            btn.remove_class("-active")
        btn_match = self.query(f"#coin-{self.active_coin.lower()}")
        if btn_match:
            btn_match.first().add_class("-active")

    def update_depth(self, depth: Dict[str, Any]) -> None:
        """Render orderbook asks, spread, bids, and depth imbalance bars."""
        coin = depth.get("coin", self.active_coin)
        bids = depth.get("bids", [])
        asks = depth.get("asks", [])
        spread = depth.get("spread", 0.0)
        spread_bps = depth.get("spread_bps", 0.0)
        top_bid = depth.get("top_bid", 0.0)
        top_ask = depth.get("top_ask", 0.0)
        imbalance = depth.get("imbalance", 1.0)
        bid_vol = depth.get("bid_vol", 0.0)
        ask_vol = depth.get("ask_vol", 0.0)

        txt = Text()
        txt.append(f"ORDERBOOK L2 DEPTH LADDER — {coin}-PERP\n\n", style="bold white")
        txt.append("       PRICE        SIZE     CUMULATIVE DEPTH\n", style="dim")
        txt.append("  ──────────────────────────────────────────────────────────\n", style="dim")

        # Top 6 Asks (reversed so lowest ask is at bottom near spread)
        asks_slice = list(reversed(asks[:6]))
        max_sz = max([float(a.get("sz", 0)) for a in asks[:6] + bids[:6]] or [1.0])
        bar_len = 24

        for a in asks_slice:
            px = float(a.get("px", 0.0))
            sz = float(a.get("sz", 0.0))
            bar_n = int((sz / max_sz) * bar_len)
            bar_str = "█" * max(1, bar_n)
            px_fmt = f"${px:,.4f}" if px < 10 else f"${px:,.2f}"
            txt.append("  ASK  ", style="bold red")
            txt.append(f"{px_fmt:>11}  {sz:>8.3f}   ", style="red")
            txt.append(f"{bar_str:<24}\n", style="red")

        # Spread Row
        mid_px = (top_ask + top_bid) / 2.0 if (top_ask > 0 and top_bid > 0) else 0.0
        mid_fmt = f"${mid_px:,.4f}" if mid_px < 10 else f"${mid_px:,.2f}"
        txt.append("  ──────────────────────────────────────────────────────────\n", style="dim")
        txt.append(f"  MID: {mid_fmt}  |  SPREAD: ${spread:.4f} ({spread_bps:.1f} bps)\n", style="bold yellow")
        txt.append("  ──────────────────────────────────────────────────────────\n", style="dim")

        # Top 6 Bids
        for b in bids[:6]:
            px = float(b.get("px", 0.0))
            sz = float(b.get("sz", 0.0))
            bar_n = int((sz / max_sz) * bar_len)
            bar_str = "█" * max(1, bar_n)
            px_fmt = f"${px:,.4f}" if px < 10 else f"${px:,.2f}"
            txt.append("  BID  ", style="bold green")
            txt.append(f"{px_fmt:>11}  {sz:>8.3f}   ", style="green")
            txt.append(f"{bar_str:<24}\n", style="green")

        self.ladder_display.update(txt)

        # Imbalance status
        imb_text = Text()
        imb_style = "bold green" if imbalance > 1.2 else ("bold red" if imbalance < 0.8 else "cyan")
        imb_bias = "BID DOMINANT" if imbalance > 1.2 else ("ASK DOMINANT" if imbalance < 0.8 else "BALANCED")
        imb_text.append("Depth Vol: ", style="dim")
        imb_text.append(f"Bid: {bid_vol:,.2f} | Ask: {ask_vol:,.2f}  ", style="white")
        imb_text.append("Imbalance Ratio: ", style="dim")
        imb_text.append(f"{imbalance:.2f}x [{imb_bias}]", style=imb_style)
        self.stats_bar.update(imb_text)
