#!/usr/bin/env python3
"""
Test script verifying the real-time analytics pipeline from the dashboard
into the desktop trading bot.
"""

import os
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.scanner.analytics_pipeline import AnalyticsPipeline

console = Console()

def test_pipeline():
    console.print(Panel("[bold cyan]Testing Hyperliquid Analytics Pipeline Bridge[/bold cyan]", border_style="cyan"))

    pipeline = AnalyticsPipeline(dashboard_port=8001, coins=["SOL", "BTC", "ETH"])

    online = pipeline.is_dashboard_online()
    console.print(f"• Dashboard API Online (http://127.0.0.1:8001): [{'green' if online else 'red'}]{online}[/]")

    console.print("• Streaming WebSocket market data for SOL (waiting up to 8s)...")
    data = None
    for _ in range(16):
        time.sleep(0.5)
        data = pipeline.get_latest("SOL")
        if data and data.get("orderbook", {}).get("mid_price"):
            break

    if not data or not data.get("orderbook", {}).get("mid_price"):
        console.print("[bold red]❌ Failed to receive live stream data from dashboard[/bold red]")
        pipeline.stop()
        return False

    console.print("[bold green]✓ Live analytics stream active![/bold green]\n")

    ob = data.get("orderbook", {})
    tf = data.get("trade_flow", {})
    reg = data.get("regime", {})
    crowd = data.get("crowding", {})
    liq = data.get("liquidations", {})

    table = Table(title="Live Dashboard Signals Received by Trading Bot", header_style="bold magenta")
    table.add_column("Category", style="cyan")
    table.add_column("Key Metric", style="white")
    table.add_column("Live Value", style="bold green")

    table.add_row("Orderbook", "Mid Price", f"${ob.get('mid_price', 0):,.2f}")
    table.add_row("Orderbook", "Spread (bps)", f"{ob.get('spread_bps', 0):.2f} bps")
    table.add_row("Orderbook", "L5 Imbalance", f"{ob.get('l5_imbalance', 0)*100:+.2f}%")
    table.add_row("Trade Flow", "Sweep Direction", f"{str(tf.get('sweep_direction') or 'none').upper()}")
    table.add_row("Trade Flow", "Buy Ratio", f"{float(tf.get('buy_ratio') or 0)*100:+.1f}%")
    table.add_row("Regime", "Market Regime", f"{str(reg.get('market_regime') or 'unknown').upper()}")
    table.add_row("Regime", "Liquidity Regime", f"{str(reg.get('liquidity_regime') or 'unknown').upper()}")
    table.add_row("Crowding", "Crowded Long / Short", f"L:{crowd.get('is_crowded_long', False)} / S:{crowd.get('is_crowded_short', False)}")
    table.add_row("Liquidations", "Status", f"{str(liq.get('status') or 'NORMAL').upper()}")

    console.print(table)

    # Test audit simulation
    audit_sim = pipeline.audit_position_risk("SOL", "LONG", pnl_pct=-0.5)
    console.print(f"\n• Position Microstructure Health Audit: Toxic = {audit_sim.is_toxic}")

    # Test entry signal filter
    allowed, reason = pipeline.get_entry_signal_filter("SOL", "LONG")
    console.print(f"• Pre-Trade Signal Filter: Allowed = {allowed} ({reason})")

    bridge_path = os.path.join(REPO_ROOT, "bridge", "analytics_microstructure.json")
    if os.path.exists(bridge_path):
        console.print(f"[bold green]✓ Atomic bridge persistence verified: {bridge_path}[/bold green]")

    pipeline.stop()
    return True

if __name__ == "__main__":
    success = test_pipeline()
    sys.exit(0 if success else 1)
