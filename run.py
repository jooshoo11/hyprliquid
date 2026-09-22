#!/usr/bin/env python3
"""
Hyperliquid Master Autonomous Trading Engine (run.py)
Single-command entry point that runs the complete autonomous system:
- Nautilus Live TradingNode (4 strategies: SMC, Funding Fade, Book Imbalance, VWAP/OI).
- Embedded AI Sentinel (5s active trade watchdog with direct native emergency closes).
- Embedded AI Prospector (15m market scanner for top 50 perpetuals).
- Embedded Modern Web Cockpit (FastAPI + WebSockets on http://localhost:8000).

Usage:
  python run.py                  # Runs in Paper Trading mode on live Mainnet data
  python run.py --port 8080      # Custom web port
  python run.py --live           # Live execution on Testnet burner wallet
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import os
import time
import signal
import argparse
import threading
import uvicorn
from rich.console import Console
from rich.panel import Panel

from src.engine.unified_engine import UnifiedEngine
from src.web.server import app, set_active_engine

console = Console()


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid Autonomous Multi-Strategy System")
    parser.add_argument("--top-n", type=int, default=30, help="Number of perpetual markets to monitor (default 30)")
    parser.add_argument("--port", type=int, default=8000, help="Web Cockpit port (default 8000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Web Cockpit bind host (default 0.0.0.0)")
    parser.add_argument("--live", action="store_true", help="Run in Live Testnet mode with burner wallet instead of local Paper emulator")
    parser.add_argument("--risk-pct", type=float, default=0.01, help="Risk percentage per trade (default 0.01 = 1%%)")
    parser.add_argument("--rr-ratio", type=float, default=2.5, help="Reward-to-risk ratio (default 2.5)")
    parser.add_argument("--watchdog-interval", type=float, default=10.0, help="Active trade watchdog audit interval in seconds (default 10.0)")
    parser.add_argument("--prospector-interval", type=float, default=900.0, help="Market prospector scan interval in seconds (default 900.0 = 15m)")
    args = parser.parse_args()

    paper_mode = not args.live

    banner = Panel.fit(
        f"[bold cyan]HYPERLIQUID UNIFIED AUTONOMOUS TRADING SYSTEM[/bold cyan]\n"
        f"• [bold]Execution Mode:[/bold] {'[green]PAPER (Mainnet Data + Local Emulator)[/green]' if paper_mode else '[yellow]LIVE (Testnet Burner Wallet)[/yellow]'}\n"
        f"• [bold]Web Cockpit:[/bold] [bold underline cyan]http://localhost:{args.port}[/bold underline cyan]\n"
        f"• [bold]AI Sentinel:[/bold] [green]ACTIVE[/green] ({int(args.watchdog_interval)}s Watchdog + {int(args.prospector_interval/60)}m Prospector)\n"
        f"• [bold]Strategies (4/4):[/bold] SMC Trend, Funding Fade, VWAP/OI Momentum, Book Scalper\n"
        f"• [bold]Risk Engine:[/bold] PortfolioGuard (Max 25% Pos, Max 10 Open, 20% Daily DD)\n"
        f"[dim]Press Ctrl+C to stop cleanly.[/dim]",
        title="🤖 Antigravity Engine",
        border_style="cyan",
    )
    console.print(banner)

    # 1. Initialize Autonomous Engine
    engine = UnifiedEngine(
        top_n=args.top_n,
        paper=paper_mode,
        risk_pct=args.risk_pct,
        rr_ratio=args.rr_ratio,
        web_port=args.port,
        watchdog_interval=args.watchdog_interval,
        prospector_interval=args.prospector_interval,
    )

    # 2. Hook engine into Web Server for direct in-memory execution
    set_active_engine(engine)

    # 3. Start Engine in background
    engine.start()

    # 4. Start Web Server in background thread
    def run_web():
        uvicorn_config = uvicorn.Config(
            app=app,
            host=args.host,
            port=args.port,
            log_level="error",
            access_log=False,
        )
        server = uvicorn.Server(uvicorn_config)
        server.run()

    t_web = threading.Thread(target=run_web, daemon=True, name="WebCockpit")
    t_web.start()

    console.print(f"\n[bold green]🚀 System running autonomously! Open your cockpit at http://localhost:{args.port}[/bold green]\n")

    # 5. Handle Graceful Shutdown
    def handle_sig(sig, frame):
        console.print("\n[bold yellow]🛑 Shutdown signal received. Stopping all systems cleanly...[/bold yellow]")
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    # Main thread keeps alive quietly
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        handle_sig(None, None)


if __name__ == "__main__":
    main()
