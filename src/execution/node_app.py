import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import argparse
import time
import datetime
import os
import json
import traceback
from typing import Optional, List, Dict, Any

from rich.text import Text
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Header, Footer, Static, Log
from textual.containers import Vertical, Horizontal

from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, ClientOrderId, StrategyId, AccountId, Venue
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.orders.market import MarketOrder
from nautilus_trader.execution.messages import SubmitOrder, CancelOrder
from nautilus_trader.core.uuid import UUID4

from src.execution.node_runner import HyperliquidNodeRunner
from src.scanner.mcp_client import HyperliquidInfoClient


class NodeDashboardApp(App):
    """
    Production-grade, fault-tolerant Terminal UI dashboard for Hyperliquid Multi-Strategy Trading.
    Displays:
    1. Top Overview: Real-time Account Equity, P&L, Margin, and AI Strategy Regimes.
    2. Active Positions: Dedicated real-time panel with live PnL, ROI, and notional sizes.
    3. Market Scanner: 30-coin universe with SMC trends, supply/demand zones, and funding APRs.
    4. Live Activity Log: Execution messages, AI Trade Manager checks, and risk events.
    """

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }
    #top_panel {
        height: 7;
        margin: 0;
        padding: 0;
    }
    #portfolio_overview {
        width: 50%;
        height: 100%;
        border: round green;
        padding: 0 1;
    }
    #ai_logic_overview {
        width: 50%;
        height: 100%;
        border: round cyan;
        padding: 0 1;
    }
    .section_title {
        height: 1;
        padding: 0 1;
        background: $panel;
        text-align: left;
    }
    #positions_container {
        height: 7;
        border: round yellow;
    }
    #scanner_container {
        height: 1fr;
        border: round blue;
    }
    #logs_container {
        height: 8;
        border: round magenta;
    }
    DataTable {
        height: 100%;
    }
    """

    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("p", "pause", "Pause/Resume"),
        ("c", "clear_logs", "Clear Logs"),
    ]

    def __init__(self, runner: HyperliquidNodeRunner):
        super().__init__()
        self.runner = runner
        self.paused = False
        self.last_trade_manager_status = "HOLD (Active trades healthy)"
        self.runner.setup()  # Initialize TradingNode kernel and strategies

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="top_panel"):
            yield Static(id="portfolio_overview")
            yield Static(id="ai_logic_overview")
        with Vertical(id="positions_container"):
            yield Static("[bold yellow]━━━ ACTIVE OPEN POSITIONS (LIVE P&L & EXECUTION) ━━━[/bold yellow]", classes="section_title")
            yield DataTable(id="positions_table")
        with Vertical(id="scanner_container"):
            yield Static("[bold cyan]━━━ MARKET SCANNER (TOP 30 PERPETUAL MARKETS) ━━━[/bold cyan]", classes="section_title")
            yield DataTable(id="market_table")
        with Vertical(id="logs_container"):
            yield Static("[bold magenta]━━━ LIVE EXECUTION & AI ACTIVITY LOGS ━━━[/bold magenta]", classes="section_title")
            yield Log(id="log_view", max_lines=500)
        yield Footer()

    def on_mount(self) -> None:
        self.log_view = self.query_one("#log_view", Log)

        # Setup Positions Table
        pos_table = self.query_one("#positions_table", DataTable)
        pos_table.cursor_type = "row"
        pos_table.zebra_stripes = True
        pos_table.add_columns(
            "Symbol", "Side", "Size", "Notional ($)", "Entry Px", "Mark Px", "PnL ($)", "ROI (%)", "Strategy"
        )

        # Setup Market Scanner Table
        table = self.query_one("#market_table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns(
            "Symbol", "Last Price", "5M Gain", "30M Gain", "24H Gain",
            "4H Trend", "30M Zones", "5M Structure", "Funding APR", "Position"
        )

        # Start Nautilus Trader node in background thread
        import threading
        self.node_thread = threading.Thread(target=self.runner.node.run, daemon=True)
        self.node_thread.start()

        # Start all strategies safely in background thread
        def start_strats():
            try:
                import time
                time.sleep(3)
                if self.runner.node and self.runner.node.trader:
                    for strat in [
                        self.runner.continuation_strat,
                        self.runner.funding_strat,
                        self.runner.scalp_strat,
                        self.runner.vwap_strat,
                    ]:
                        if strat and not strat.is_running:
                            self.runner.node.trader.start_strategy(strat.id)
                    self.call_from_thread(self.log_view.write_line, "[bold green]All 4 modular strategies started under PortfolioGuard![/bold green]")
            except Exception as e:
                self.call_from_thread(self.log_view.write_line, f"[yellow]Strategy startup notice: {e}[/yellow]")

        threading.Thread(target=start_strats, daemon=True).start()
        self.log_view.write_line("[bold green]TradingNode started with live Mainnet data stream.[/bold green]")

        # Set 1-second UI refresh timer
        self.update_timer = self.set_interval(1.0, self.update_dashboard)
        self.log_view.write_line(f"Surveillance active across {len(self.runner.top_coins)} perpetual markets.")

    def action_quit_app(self) -> None:
        self.log_view.write_line("[bold red]Shutting down TradingNode gracefully...[/bold red]")
        try:
            # Persist final paper state before exit
            if self.runner.paper:
                cash_balance = self._get_account_cash()
                final_positions = self._get_open_positions()
                ctx_map = self._fetch_asset_contexts()
                total_unrealized = 0.0
                positions_data = []
                for pos in final_positions:
                    try:
                        coin = pos.instrument_id.symbol.value.split("-")[0]
                        entry_px = float(pos.avg_px_open) if hasattr(pos, "avg_px_open") else 0.0
                        ctx = ctx_map.get(coin, {})
                        cur_px = float(ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx", 0.0))
                        qty = pos.quantity.as_double()
                        unrealized = round((cur_px - entry_px) * qty * (1.0 if pos.is_long else -1.0), 2) if (cur_px > 0 and entry_px > 0) else 0.0
                        total_unrealized += unrealized
                        positions_data.append({
                            "coin": coin,
                            "instrument_id": str(pos.instrument_id),
                            "side": "LONG" if pos.is_long else "SHORT",
                            "size": qty,
                            "entry_price": entry_px,
                            "unrealized_pnl": unrealized,
                        })
                    except Exception:
                        pass
                final_equity = self._get_account_equity(cash_balance, total_unrealized)
                self.runner.save_paper_state(
                    equity=final_equity,
                    realized_pnl=cash_balance - 100.0,
                    positions=positions_data,
                )
                self.log_view.write_line(f"[bold cyan]💾 Paper state saved: Total Equity=${final_equity:.2f} (Cash=${cash_balance:.2f})[/bold cyan]")
        except Exception:
            pass
        try:
            self.runner.stop()
        except Exception:
            pass
        self.exit()

    def action_pause(self) -> None:
        self.paused = not self.paused
        state = "PAUSED" if self.paused else "RESUMED"
        self.log_view.write_line(f"[yellow]UI updates {state}[/yellow]")

    def action_clear_logs(self) -> None:
        try:
            self.log_view.clear()
        except Exception:
            pass

    def update_dashboard(self) -> None:
        """Top-level, crash-proof dashboard refresh cycle."""
        if self.paused:
            return

        try:
            self._process_stale_orders()
            self._sync_prospects()
            ctx_map = self._fetch_asset_contexts()
            open_positions = self._get_open_positions()

            # Compute cash balance, unrealized PnL, and total notional exposure
            cash_balance = self._get_account_cash()
            total_unrealized = 0.0
            total_notional = 0.0
            for pos in open_positions:
                try:
                    coin = pos.instrument_id.symbol.value.split("-")[0]
                    ctx = ctx_map.get(coin, {})
                    cur_px = float(ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx", 0.0))
                    entry = float(pos.avg_px_open) if hasattr(pos, "avg_px_open") else (pos.avg_px.as_double() if pos.avg_px else 0.0)
                    qty = pos.quantity.as_double()
                    if cur_px > 0 and entry > 0:
                        pnl = (cur_px - entry) * qty * (1.0 if pos.is_long else -1.0)
                        total_unrealized += pnl
                        total_notional += (cur_px * qty)
                except Exception:
                    pass

            total_equity = self._get_account_equity(cash_balance, total_unrealized)

            # Render UI components safely
            self._render_top_overview(total_equity, cash_balance, total_unrealized, total_notional, open_positions, ctx_map)
            self._render_positions_table(open_positions, ctx_map)
            self._render_market_table(ctx_map, open_positions)

            # Synchronize JSON Bridge for AI Agents
            self._sync_bridge(total_equity, cash_balance, open_positions, ctx_map)
            self._process_ai_commands(open_positions)

        except Exception as e:
            try:
                self.log_view.write_line(f"[red]Dashboard update warning: {e}[/red]")
            except Exception:
                pass

    def _process_stale_orders(self) -> None:
        """Cancel orders that have exceeded their time-to-live."""
        try:
            stale_orders = self.runner.guard.get_stale_orders_to_cancel()
            for stale in stale_orders:
                try:
                    cancel_cmd = CancelOrder(
                        trader_id=self.runner.node.trader_id,
                        strategy_id=stale.strategy_id,
                        instrument_id=stale.instrument_id,
                        client_order_id=stale.order_id,
                    )
                    self.runner.node.trader.execute(cancel_cmd)
                    self.log_view.write_line(f"[yellow]Cancelled stale order {stale.order_id}[/yellow]")
                except Exception:
                    pass
                self.runner.guard.acknowledge_order_cancelled(stale.order_id)
        except Exception:
            pass

    def _sync_prospects(self) -> None:
        """Poll latest AI Prospector biases from bridge/prospects.json."""
        try:
            if hasattr(self.runner, "load_prospects"):
                self.runner.load_prospects()
        except Exception:
            pass

    def _fetch_asset_contexts(self) -> Dict[str, Any]:
        """Retrieve real-time market contexts with zero-freeze fallback."""
        try:
            meta, asset_ctxs = self.runner.info_client.get_meta_and_asset_ctxs()
            return {m.get("name"): ctx for m, ctx in zip(meta.get("universe", []), asset_ctxs)}
        except Exception:
            return {}

    def _get_open_positions(self) -> List[Any]:
        """Fetch all active open positions from Nautilus cache."""
        try:
            if self.runner.node and self.runner.node.cache:
                return [p for p in self.runner.node.cache.positions_open() if not p.is_closed]
        except Exception:
            pass
        return []

    def _get_account_cash(self) -> float:
        """Fetch current cash balance (realized equity) from portfolio account."""
        try:
            if self.runner.node and self.runner.node.portfolio:
                venue = Venue("HYPERLIQUID")
                acct = self.runner.node.portfolio.account(venue=venue)
                if acct:
                    bal = acct.balance_total(USD)
                    if bal:
                        return bal.as_double()
        except Exception:
            pass
        return getattr(self.runner, "_paper_starting_equity", 100.0)

    def _get_account_equity(self, cash_balance: Optional[float] = None, total_unrealized: float = 0.0) -> float:
        """Fetch current total equity (cash balance + unrealized PnL)."""
        if cash_balance is None:
            cash_balance = self._get_account_cash()
        return round(cash_balance + total_unrealized, 2)

    def _render_top_overview(
        self,
        equity: float,
        cash_balance: float,
        total_unrealized: float,
        total_notional: float,
        open_positions: List[Any],
        ctx_map: Dict[str, Any],
    ) -> None:
        """Render the top portfolio and AI strategy logic panels."""
        try:
            pnl_color = "bold green" if total_unrealized >= 0 else "bold red"
            pnl_sign = "+" if total_unrealized >= 0 else ""
            roi = (total_unrealized / cash_balance * 100.0) if cash_balance > 0 else 0.0
            roi_sign = "+" if roi >= 0 else ""

            port_text = (
                f"[bold]PORTFOLIO OVERVIEW & RISK[/bold]\n"
                f"• [bold]Total Equity:[/bold] ${equity:,.2f} | [bold]Cash Balance:[/bold] ${cash_balance:,.2f} | [bold]Unrealized P&L:[/bold] [{pnl_color}]{pnl_sign}${total_unrealized:,.2f} ({roi_sign}{roi:.2f}%)[/{pnl_color}]\n"
                f"• [bold]Open Positions:[/bold] {len(open_positions)} Active | [bold]Notional Exposure:[/bold] ${total_notional:,.2f}\n"
                f"• [bold]Portfolio Guard:[/bold] [bold green]ACTIVE[/bold green] (Max 25% Pos, Max 10 Open, 20% Daily DD)"
            )
            self.query_one("#portfolio_overview", Static).update(port_text)

            # 2. Render AI Strategy & Logic Overview
            prospects_text = "Scanning..."
            if hasattr(self.runner, "prospect_biases") and self.runner.prospect_biases:
                items = [f"{c} [{'green' if b=='LONG' else 'red'}]{b}[/]" for c, b in list(self.runner.prospect_biases.items())[:5]]
                prospects_text = ", ".join(items)

            ai_text = (
                f"[bold]AI TRADE LOGIC & ANALYSIS[/bold]\n"
                f"• [bold]AI Prospector Targets:[/bold] {prospects_text}\n"
                f"• [bold]AI Trade Manager:[/bold] [bold green]{escape(self.last_trade_manager_status)}[/bold green]\n"
                f"• [bold]Strategies (4/4):[/bold] [cyan]SMC Trend[/cyan] • [cyan]Funding Fade[/cyan] • [cyan]VWAP/OI[/cyan] • [cyan]Book Scalp[/cyan]"
            )
            self.query_one("#ai_logic_overview", Static).update(ai_text)
        except Exception:
            pass

    def _render_positions_table(self, open_positions: List[Any], ctx_map: Dict[str, Any]) -> None:
        """Render the dedicated Open Positions table."""
        try:
            pos_table = self.query_one("#positions_table", DataTable)
            pos_table.clear()

            if not open_positions:
                pos_table.add_row(
                    Text.from_markup("[dim]No active positions[/dim]"),
                    Text.from_markup("[dim]FLAT[/dim]"),
                    "-", "-", "-", "-",
                    Text.from_markup("[dim]$0.00[/dim]"),
                    Text.from_markup("[dim]0.00%[/dim]"),
                    Text.from_markup("[dim]Scanning markets...[/dim]"),
                )
                return

            for pos in open_positions:
                try:
                    coin = pos.instrument_id.symbol.value.split("-")[0]
                    ctx = ctx_map.get(coin, {})
                    cur_px = float(ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx", 0.0))
                    entry = float(pos.avg_px_open) if hasattr(pos, "avg_px_open") else (pos.avg_px.as_double() if pos.avg_px else 0.0)
                    qty = pos.quantity.as_double()
                    notional = (cur_px * qty) if cur_px > 0 else (entry * qty)

                    pnl = (cur_px - entry) * qty * (1.0 if pos.is_long else -1.0) if cur_px > 0 and entry > 0 else 0.0
                    cost = entry * qty if entry > 0 else 1.0
                    roi = (pnl / cost) * 100.0 if cost > 0 else 0.0

                    side_color = "bold green" if pos.is_long else "bold red"
                    side_str = "LONG" if pos.is_long else "SHORT"
                    pnl_color = "bold green" if pnl >= 0 else "bold red"
                    pnl_sign = "+" if pnl >= 0 else ""
                    roi_sign = "+" if roi >= 0 else ""

                    strat_name = str(pos.strategy_id).split("-")[0] if pos.strategy_id else "SMC-Trend"

                    pos_table.add_row(
                        Text.from_markup(f"[bold]{coin}[/bold]"),
                        Text.from_markup(f"[{side_color}]{side_str}[/{side_color}]"),
                        f"{qty:,.2f}",
                        f"${notional:,.2f}",
                        f"${entry:,.4f}" if entry < 1.0 else f"${entry:,.2f}",
                        f"${cur_px:,.4f}" if cur_px < 1.0 else f"${cur_px:,.2f}",
                        Text.from_markup(f"[{pnl_color}]{pnl_sign}${pnl:,.2f}[/{pnl_color}]"),
                        Text.from_markup(f"[{pnl_color}]{roi_sign}{roi:.2f}%[/{pnl_color}]"),
                        Text.from_markup(f"[dim]{strat_name}[/dim]")
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _render_market_table(self, ctx_map: Dict[str, Any], open_positions: List[Any]) -> None:
        """Render the 30-coin market scanner table."""
        try:
            table = self.query_one("#market_table", DataTable)
            open_coins = {p.instrument_id.symbol.value.split("-")[0] for p in open_positions}

            rows_to_add = []
            for idx, coin in enumerate(self.runner.top_coins):
                try:
                    instr_id_str = f"{coin}-USD-PERP.HYPERLIQUID"
                    cont_state = self.runner.continuation_strat.states.get(instr_id_str) if self.runner.continuation_strat else None

                    ctx = ctx_map.get(coin, {})
                    mid_px = float(ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx", 0.0))
                    current_px = mid_px
                    prev_day_px = float(ctx.get("prevDayPx", 0.0))

                    # Format price
                    if current_px >= 1000:
                        price_str = f"${current_px:,.2f}"
                    elif current_px >= 1.0:
                        price_str = f"${current_px:,.3f}"
                    elif current_px > 0:
                        price_str = f"${current_px:,.4f}"
                    else:
                        price_str = "Awaiting"

                    # 24H gain
                    gain_24h = ((current_px - prev_day_px) / prev_day_px) * 100 if prev_day_px and current_px else 0.0
                    g24_color = "green" if gain_24h >= 0 else "red"
                    g24_str = f"[{g24_color}]{'+' if gain_24h>=0 else ''}{gain_24h:.2f}%[/{g24_color}]"

                    # 5M gain
                    gain_5m_str = "[dim]-[/dim]"
                    if cont_state and current_px and cont_state.last_5m_bar:
                        ref_5m = cont_state.last_5m_bar.close.as_double()
                        if ref_5m > 0:
                            g5 = ((current_px - ref_5m) / ref_5m) * 100
                            g5_color = "green" if g5 >= 0 else "red"
                            gain_5m_str = f"[{g5_color}]{'+' if g5>=0 else ''}{g5:.2f}%[/{g5_color}]"

                    # 30M gain
                    gain_30m_str = "[dim]-[/dim]"
                    ref_30m_bar = cont_state.last_30m_bar if cont_state and cont_state.last_30m_bar else (cont_state.recent_30m_bars[-1] if cont_state and cont_state.recent_30m_bars else None)
                    if ref_30m_bar and current_px:
                        ref_30m = ref_30m_bar.close.as_double()
                        if ref_30m > 0:
                            g30 = ((current_px - ref_30m) / ref_30m) * 100
                            g30_color = "green" if g30 >= 0 else "red"
                            gain_30m_str = f"[{g30_color}]{'+' if g30>=0 else ''}{g30:.2f}%[/{g30_color}]"

                    # 4H Trend
                    trend_str = "[dim]NEUTRAL[/dim]"
                    if cont_state:
                        if cont_state.trend_state == "BULLISH":
                            trend_str = "[bold green]BULL (50>200)[/bold green]"
                        elif cont_state.trend_state == "BEARISH":
                            trend_str = "[bold red]BEAR (50<200)[/bold red]"
                        elif cont_state.ema_50_4h.initialized and cont_state.ema_200_4h.initialized:
                            trend_str = "[yellow]CHOP/RANGE[/yellow]"

                    # 30M Zones
                    if cont_state:
                        num_d = len(cont_state.demand_zones)
                        num_s = len(cont_state.supply_zones)
                        zones_str = f"[green]{num_d}D[/green] / [red]{num_s}S[/red]"
                    else:
                        zones_str = "[dim]Scanning...[/dim]"

                    # 5M Structure
                    if cont_state:
                        if cont_state.zone_in_play:
                            zt = cont_state.zone_in_play.zone_type
                            z_color = "green" if zt == "DEMAND" else "red"
                            struct_str = f"[bold {z_color}]TEST {zt}[/bold {z_color}]"
                        elif cont_state.recent_swing_high and cont_state.recent_swing_low:
                            sh = cont_state.recent_swing_high
                            sl = cont_state.recent_swing_low
                            if current_px > sh:
                                struct_str = "[bold green]MSS BULL BREAK[/bold green]"
                            elif current_px < sl:
                                struct_str = "[bold red]MSS BEAR BREAK[/bold red]"
                            else:
                                struct_str = f"[dim]{sl:g} - {sh:g}[/dim]"
                        else:
                            struct_str = "[dim]Building...[/dim]"
                    else:
                        struct_str = "[dim]Scanning...[/dim]"

                    # Funding APR
                    funding_apr = float(ctx.get("funding", 0.0)) * 365 * 100 * 24 if ctx else 0.0
                    fund_color = "red" if funding_apr > 50 else ("green" if funding_apr < -30 else "yellow")
                    fund_str = f"[{fund_color}]{'+' if funding_apr>0 else ''}{funding_apr:.1f}%[/{fund_color}]"

                    # Position badge
                    is_active = coin in open_coins
                    pos_badge = "[bold yellow]● IN TRADE[/bold yellow]" if is_active else "[dim]FLAT[/dim]"

                    rows_to_add.append({
                        "is_active": is_active,
                        "coin": coin,
                        "idx": idx,
                        "data": (
                            Text.from_markup(f"[bold]{coin}[/bold]"),
                            Text.from_markup(price_str),
                            Text.from_markup(gain_5m_str),
                            Text.from_markup(gain_30m_str),
                            Text.from_markup(g24_str),
                            Text.from_markup(trend_str),
                            Text.from_markup(zones_str),
                            Text.from_markup(struct_str),
                            Text.from_markup(fund_str),
                            Text.from_markup(pos_badge)
                        )
                    })
                except Exception:
                    pass

            # Sort: active trades float to top
            rows_to_add.sort(key=lambda x: (not x["is_active"], x["idx"]))

            if table.row_count != len(rows_to_add):
                table.clear()
                for r in rows_to_add:
                    table.add_row(*r["data"], key=r["coin"])
            else:
                for r in rows_to_add:
                    for col_idx, val in enumerate(r["data"]):
                        table.update_cell(str(r["coin"]), table.columns[list(table.columns.keys())[col_idx]].key, val)

        except Exception:
            pass

    def _sync_bridge(self, equity: float, cash_balance: float, open_positions: List[Any], ctx_map: Dict[str, Any]) -> None:
        """Safely export live state to bridge/active_trades.json and persist paper state."""
        try:
            bridge_path = os.path.join(os.getcwd(), "bridge", "active_trades.json")
            positions_data = []
            for pos in open_positions:
                try:
                    coin = pos.instrument_id.symbol.value.split("-")[0]
                    entry_px = float(pos.avg_px_open) if hasattr(pos, "avg_px_open") else (pos.avg_px.as_double() if pos.avg_px else 0.0)
                    ctx = ctx_map.get(coin, {})
                    cur_px = float(ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx", 0.0))
                    qty = pos.quantity.as_double()
                    unrealized = 0.0
                    if cur_px > 0 and entry_px > 0 and qty > 0:
                        unrealized = round((cur_px - entry_px) * qty * (1.0 if pos.is_long else -1.0), 2)
                    else:
                        try:
                            pnl_money = self.runner.node.portfolio.unrealized_pnl(pos.instrument_id)
                            unrealized = round(pnl_money.as_double(), 2) if pnl_money else 0.0
                        except Exception:
                            pass

                    positions_data.append({
                        "coin": coin,
                        "instrument_id": str(pos.instrument_id),
                        "side": "LONG" if pos.is_long else "SHORT",
                        "size": qty,
                        "entry_price": entry_px,
                        "unrealized_pnl": unrealized,
                    })
                except Exception:
                    pass

            state = {
                "timestamp": time.time(),
                "equity": round(equity, 2),
                "cash_balance": round(cash_balance, 2),
                "positions": positions_data,
            }
            tmp_path = f"{bridge_path}.tmp"
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, bridge_path)

            # Persist paper state so restarts don't wipe the balance
            if self.runner.paper:
                realized_pnl = cash_balance - 100.0  # net gain vs original $100 starting balance
                self.runner.save_paper_state(
                    equity=equity,
                    realized_pnl=realized_pnl,
                    positions=positions_data,
                )
        except Exception:
            pass

    def _process_ai_commands(self, open_positions: List[Any]) -> None:
        """Safely execute any pending commands from bridge/ai_commands.json."""
        cmds_path = os.path.join(os.getcwd(), "bridge", "ai_commands.json")
        if not os.path.exists(cmds_path):
            return

        try:
            with open(cmds_path, "r") as cf:
                try:
                    commands = json.load(cf)
                except Exception:
                    commands = []

            if not commands:
                return

            for cmd in commands:
                if cmd.get("action") == "CLOSE_POSITION":
                    coin_target = cmd.get("coin")
                    reason = cmd.get("reason", "AI Trade Manager Abort")
                    self.last_trade_manager_status = f"ABORT: {coin_target} ({reason[:30]})"
                    self.log_view.write_line(f"[bold magenta]🚨 AI AGENT COMMANDED CLOSE: {coin_target} - {reason}[/bold magenta]")

                    coin_clean = str(coin_target).upper().split("-")[0].split(".")[0]
                    instr_id = InstrumentId.from_str(f"{coin_clean}-USD-PERP.HYPERLIQUID")
                    matches = [p for p in open_positions if p.instrument_id == instr_id]

                    for pos in matches:
                        try:
                            target_strat = None
                            for strat in [
                                self.runner.funding_strat,
                                self.runner.continuation_strat,
                                self.runner.scalp_strat,
                                self.runner.vwap_strat,
                            ]:
                                if strat and strat.id == pos.strategy_id:
                                    target_strat = strat
                                    break
                            if target_strat is None:
                                target_strat = self.runner.funding_strat or self.runner.continuation_strat

                            if target_strat:
                                target_strat.close_position(pos)
                                self.log_view.write_line(f"[bold green]✅ Market close submitted for {coin_target} via {target_strat.id}[/bold green]")
                            else:
                                self.log_view.write_line(f"[red]No active strategy found to close {coin_target}[/red]")
                        except Exception as close_err:
                            self.log_view.write_line(f"[red]Failed to execute AI close on {coin_target}: {close_err}[/red]")

            with open(cmds_path, "w") as cf:
                json.dump([], cf)

        except Exception as e:
            try:
                self.log_view.write_line(f"[red]Bridge command error: {e}[/red]")
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Interactive Hyperliquid Node Dashboard")
    parser.add_argument("--top-n", type=int, default=30, help="Number of perpetual markets to monitor")
    parser.add_argument("--paper", action="store_true", help="Use local OrderEmulator on Mainnet data")
    args = parser.parse_args()

    runner = HyperliquidNodeRunner(
        top_n=args.top_n,
        paper=args.paper,
    )

    app = NodeDashboardApp(runner)
    app.run()
