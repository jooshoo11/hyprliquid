"""
AI Sentinel & Active Trade Watchdog (ai_sentinel.py)
Autonomous background sentinel service providing:
1. Real-Time Active Trade Sentry (10s loop):
   - Watches bridge/active_trades.json for newly opened positions.
   - When a trade opens: immediately queries live L2 order book depth and funding rates.
   - Audits trade health for toxic conditions (adverse funding flip, depth wall collapse, severe drawdown).
   - If risk thresholds are breached, writes emergency CLOSE_POSITION to bridge/ai_commands.json.
2. High-Frequency Top 10 Prospect Scanner (15-min loop):
   - Scans top 50 markets on Hyperliquid by 24h volume and funding rates.
   - Filters and ranks the Top 10 highest-conviction prospects (spot momentum, negative carry squeeze, funding fades).
   - Atomically updates bridge/prospects.json.
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import os
import time
import json
import signal
import argparse
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import polars as pl
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from src.scanner.mcp_client import HyperliquidInfoClient

console = Console()


class AISentinel:
    """
    Autonomous AI sentinel monitoring live open trades and refreshing top 10 prospects.
    """

    def __init__(
        self,
        trade_check_interval: float = 10.0,
        prospect_interval: float = 900.0,
    ):
        self.trade_check_interval = trade_check_interval
        self.prospect_interval = prospect_interval
        self.info_client = HyperliquidInfoClient()

        self.active_trades_path = os.path.join(REPO_ROOT, "bridge", "active_trades.json")
        self.ai_commands_path = os.path.join(REPO_ROOT, "bridge", "ai_commands.json")
        self.prospects_path = os.path.join(REPO_ROOT, "bridge", "prospects.json")

        self.last_prospect_scan: float = 0.0
        self._running: bool = True
        self._last_positions_seen: List[Dict[str, Any]] = []

    def stop(self, *args) -> None:
        """Graceful shutdown handler."""
        console.print("\n[bold yellow]🛑 AI Sentinel shutting down cleanly...[/bold yellow]")
        self._running = False

    def scan_top_prospects(self) -> Dict[str, Any]:
        """
        Scan all markets, filter top 50 by volume, and extract Top 10 highest-conviction prospects.
        """
        console.print("[bold cyan]🔍 [AI Prospector] Scanning top 50 Hyperliquid markets...[/bold cyan]")
        try:
            meta, asset_ctxs = self.info_client.get_meta_and_asset_ctxs()
            universe = meta.get("universe", [])

            records = []
            for u, ctx in zip(universe, asset_ctxs):
                name = u.get("name")
                px = float(ctx.get("oraclePx", 0.0))
                prev_px = float(ctx.get("prevDayPx", 0.0))
                funding = float(ctx.get("funding", 0.0))
                funding_apr = funding * 24 * 365
                vol_24h = float(ctx.get("dayNtlVlm", 0.0))
                change_24h = ((px - prev_px) / prev_px * 100) if prev_px > 0 else 0.0
                records.append({
                    "coin": name,
                    "price": px,
                    "funding_apr": funding_apr,
                    "funding_hourly": funding,
                    "vol_24h": vol_24h,
                    "change_24h": change_24h,
                })

            df = pl.DataFrame(records).sort("vol_24h", descending=True).head(50)
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            prospects: Dict[str, Any] = {}

            # 1. Spot-Led Long Momentum (top volume, healthy low funding < +25% APR, positive 24h)
            long_candidates = df.filter(
                (pl.col("change_24h") > 3.0) &
                (pl.col("funding_apr") < 0.25) &
                (pl.col("funding_apr") >= 0.0)
            ).sort("vol_24h", descending=True).head(4)

            for r in long_candidates.iter_rows(named=True):
                coin = r["coin"]
                px = r["price"]
                apr = r["funding_apr"] * 100
                chg = r["change_24h"]
                vol = r["vol_24h"] / 1e6
                prospects[coin] = {
                    "bias": "LONG",
                    "target_entry": round(px * 0.998, 4),
                    "reason": f"Spot-led momentum: +{chg:.1f}% 24h on ${vol:.1f}M vol; healthy low funding APR {apr:+.1f}%.",
                    "updated_at": now_iso,
                }

            # 2. Extreme Negative Funding (Short Squeeze Candidates)
            squeeze_candidates = df.filter(pl.col("funding_apr") < -0.30).sort("funding_apr").head(2)
            for r in squeeze_candidates.iter_rows(named=True):
                coin = r["coin"]
                px = r["price"]
                apr = r["funding_apr"] * 100
                prospects[coin] = {
                    "bias": "LONG",
                    "target_entry": round(px * 0.995, 4),
                    "reason": f"Extreme negative funding: APR {apr:+.1f}% (hourly {r['funding_hourly']:+.5f}); short carry bleeding.",
                    "updated_at": now_iso,
                }

            # 3. Crowded Long Fades (Extreme Positive Funding > +70% APR)
            fade_candidates = df.filter(pl.col("funding_apr") > 0.70).sort("funding_apr", descending=True).head(4)
            for r in fade_candidates.iter_rows(named=True):
                coin = r["coin"]
                px = r["price"]
                apr = r["funding_apr"] * 100
                chg = r["change_24h"]
                vol = r["vol_24h"] / 1e6
                prospects[coin] = {
                    "bias": "SHORT",
                    "target_entry": round(px * 1.002, 4),
                    "reason": f"Crowded long fade: Funding APR {apr:+.1f}% on ${vol:.1f}M vol (+{chg:.1f}% 24h); longs overleveraged.",
                    "updated_at": now_iso,
                }

            # Fallback if less than 10: add top volume leaders
            if len(prospects) < 10:
                for r in df.iter_rows(named=True):
                    coin = r["coin"]
                    if coin not in prospects:
                        bias = "LONG" if r["change_24h"] >= 0 else "SHORT"
                        prospects[coin] = {
                            "bias": bias,
                            "target_entry": r["price"],
                            "reason": f"Top volume leader (${r['vol_24h']/1e6:.1f}M vol, {r['change_24h']:+.1f}% 24h).",
                            "updated_at": now_iso,
                        }
                    if len(prospects) >= 10:
                        break

            # Write atomically to bridge/prospects.json
            tmp_path = self.prospects_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(prospects, f, indent=2)
            os.replace(tmp_path, self.prospects_path)

            self.last_prospect_scan = time.time()

            table = Table(title="Top 10 High-Conviction Prospects Updated", header_style="bold magenta")
            table.add_column("Coin", style="bold white")
            table.add_column("Bias", justify="center")
            table.add_column("Target Entry", justify="right", style="cyan")
            table.add_column("Rationale", style="dim")

            for c, data in prospects.items():
                b = data["bias"]
                b_color = "bold green" if b == "LONG" else "bold red"
                table.add_row(c, f"[{b_color}]{b}[/{b_color}]", f"${data['target_entry']:,.4f}", data["reason"][:65] + "...")

            console.print(table)
            return prospects

        except Exception as e:
            console.print(f"[red]Error during prospect scan: {e}[/red]")
            return {}

    def audit_active_trades(self) -> None:
        """
        Check bridge/active_trades.json. If positions are open, audit them against live L2 orderbook and funding.
        If toxic conditions are detected, issue emergency CLOSE_POSITION to bridge/ai_commands.json.
        """
        if not os.path.exists(self.active_trades_path):
            return

        try:
            with open(self.active_trades_path, "r") as f:
                data = json.load(f)

            positions = data.get("positions", [])
            equity = data.get("equity", 100.0)

            if not positions:
                # No active trades
                return

            console.print(f"\n[bold yellow]🛡️ [AI Trade Sentry] Auditing {len(positions)} active trade(s) (Equity: ${equity:.2f})...[/bold yellow]")

            emergency_commands = []

            for pos in positions:
                coin = pos.get("coin", "").upper()
                side = pos.get("side", "").upper()
                size = float(pos.get("size", 0.0))
                entry_px = float(pos.get("entry_price", 0.0))
                unrealized_pnl = float(pos.get("unrealized_pnl", 0.0))

                # Fetch live market data for this exact coin
                live_funding_apr = self.info_client.get_funding_rate(coin)
                l2 = self.info_client.get_l2_snapshot(coin)

                live_px = entry_px
                bids_depth = 0.0
                asks_depth = 0.0

                if l2.get("status") == "SUCCESS":
                    bids = l2.get("bids", [])
                    asks = l2.get("asks", [])
                    if bids and asks:
                        live_px = (float(bids[0]["px"]) + float(asks[0]["px"])) / 2.0
                        bids_depth = sum(float(b["sz"]) * float(b["px"]) for b in bids[:5])
                        asks_depth = sum(float(a["sz"]) * float(a["px"]) for a in asks[:5])

                # Calculate live metrics
                pnl_pct = ((live_px - entry_px) / entry_px * 100) if side == "LONG" else ((entry_px - live_px) / entry_px * 100) if entry_px > 0 else 0.0

                console.print(
                    f"  ▸ [bold white]{coin}[/bold white] ({side}): Entry=${entry_px:,.4f} | Live=${live_px:,.4f} | "
                    f"PnL={pnl_pct:+.2f}% (${unrealized_pnl:+.2f}) | Funding APR={live_funding_apr*100:+.1f}% | "
                    f"Depth Skew: Bids ${bids_depth/1e3:.1f}k vs Asks ${asks_depth/1e3:.1f}k"
                )

                # Toxic Condition 1: Adverse Funding Flip
                # If LONG and funding spikes > +120% APR (paying extreme carry)
                # If SHORT and funding plunges < -120% APR (paying extreme carry)
                if side == "LONG" and live_funding_apr > 1.20:
                    toxic_reason = f"Adverse funding spike to +{live_funding_apr*100:.1f}% APR (toxic long carry)"
                    console.print(f"  [bold red]🚨 EMERGENCY TRIGGER: {coin} - {toxic_reason}[/bold red]")
                    emergency_commands.append({"action": "CLOSE_POSITION", "coin": coin, "reason": toxic_reason})
                    continue

                if side == "SHORT" and live_funding_apr < -1.20:
                    toxic_reason = f"Adverse funding plunge to {live_funding_apr*100:.1f}% APR (toxic short carry)"
                    console.print(f"  [bold red]🚨 EMERGENCY TRIGGER: {coin} - {toxic_reason}[/bold red]")
                    emergency_commands.append({"action": "CLOSE_POSITION", "coin": coin, "reason": toxic_reason})
                    continue

                # Toxic Condition 2: Order Book Liquidity Collapse
                # If LONG and asks depth > 4x bids depth (heavy selling wall pushing price down)
                if side == "LONG" and bids_depth > 0 and (asks_depth / bids_depth) > 4.0 and pnl_pct < -0.5:
                    toxic_reason = f"Order book collapsed: Ask depth wall ${asks_depth/1e3:.1f}k > 4x Bid depth ${bids_depth/1e3:.1f}k"
                    console.print(f"  [bold red]🚨 EMERGENCY TRIGGER: {coin} - {toxic_reason}[/bold red]")
                    emergency_commands.append({"action": "CLOSE_POSITION", "coin": coin, "reason": toxic_reason})
                    continue

                # If SHORT and bids depth > 4x asks depth (heavy buying wall squeezing shorts)
                if side == "SHORT" and asks_depth > 0 and (bids_depth / asks_depth) > 4.0 and pnl_pct < -0.5:
                    toxic_reason = f"Order book collapsed: Bid depth wall ${bids_depth/1e3:.1f}k > 4x Ask depth ${asks_depth/1e3:.1f}k"
                    console.print(f"  [bold red]🚨 EMERGENCY TRIGGER: {coin} - {toxic_reason}[/bold red]")
                    emergency_commands.append({"action": "CLOSE_POSITION", "coin": coin, "reason": toxic_reason})
                    continue

                # Toxic Condition 3: Emergency Drawdown Breached (> -1.5% without support)
                if pnl_pct < -1.5:
                    toxic_reason = f"Adverse momentum: PnL dropped to {pnl_pct:.2f}%, exceeding 1.5% qualitative tolerance"
                    console.print(f"  [bold red]🚨 EMERGENCY TRIGGER: {coin} - {toxic_reason}[/bold red]")
                    emergency_commands.append({"action": "CLOSE_POSITION", "coin": coin, "reason": toxic_reason})
                    continue

            # If any emergency command generated, write to bridge/ai_commands.json
            if emergency_commands:
                existing_commands = []
                if os.path.exists(self.ai_commands_path):
                    try:
                        with open(self.ai_commands_path, "r") as cf:
                            existing_commands = json.load(cf)
                    except Exception:
                        existing_commands = []

                existing_commands.extend(emergency_commands)
                tmp_cmd_path = self.ai_commands_path + ".tmp"
                with open(tmp_cmd_path, "w") as cf:
                    json.dump(existing_commands, cf, indent=2)
                os.replace(tmp_cmd_path, self.ai_commands_path)
                console.print(f"[bold red]⚡ Dispatched {len(emergency_commands)} emergency CLOSE command(s) to bridge/ai_commands.json![/bold red]")

        except Exception as e:
            console.print(f"[red]Error during active trade audit: {e}[/red]")

    def run(self) -> None:
        """Main sentinel execution loop."""
        console.print(Panel(
            "[bold cyan]AI SENTINEL & ACTIVE TRADE WATCHDOG STARTED[/bold cyan]\n"
            f"Active Trade Watcher: [bold green]Every {self.trade_check_interval:.0f}s[/bold green] | "
            f"Top 10 Prospect Scanner: [bold green]Every {self.prospect_interval/60:.0f}m[/bold green]\n"
            "Monitoring: [bold white]bridge/active_trades.json[/bold white] ⟷ [bold white]bridge/ai_commands.json[/bold white]",
            border_style="cyan"
        ))

        # Initial scan on startup
        self.scan_top_prospects()

        while self._running:
            try:
                # 1. Active Trade Audit (runs frequently every 10s)
                self.audit_active_trades()

                # 2. Prospect Update (runs every 15m)
                if (time.time() - self.last_prospect_scan) >= self.prospect_interval:
                    self.scan_top_prospects()

                time.sleep(self.trade_check_interval)

            except KeyboardInterrupt:
                break
            except Exception as e:
                console.print(f"[red]Sentinel loop error: {e}[/red]")
                time.sleep(5.0)

        console.print("[green]AI Sentinel stopped.[/green]")


def main():
    parser = argparse.ArgumentParser(description="AI Sentinel & Active Trade Watchdog")
    parser.add_argument("--trade-interval", type=float, default=10.0, help="Interval in seconds to check active trades (default: 10s)")
    parser.add_argument("--prospect-interval", type=float, default=900.0, help="Interval in seconds to update prospects (default: 900s / 15m)")
    parser.add_argument("--once", action="store_true", help="Run once for testing and exit")
    args = parser.parse_args()

    sentinel = AISentinel(
        trade_check_interval=args.trade_interval,
        prospect_interval=args.prospect_interval,
    )

    signal.signal(signal.SIGINT, sentinel.stop)
    signal.signal(signal.SIGTERM, sentinel.stop)

    if args.once:
        sentinel.scan_top_prospects()
        sentinel.audit_active_trades()
    else:
        sentinel.run()


if __name__ == "__main__":
    main()
