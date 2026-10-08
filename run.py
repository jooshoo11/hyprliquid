#!/usr/bin/env python3
"""
Hyperliquid Master Autonomous Trading Engine & WezTerm Cockpit (run.py)
Single-command entry point that runs the complete autonomous system:
- Nautilus Live TradingNode (5 strategies: SMC, Funding Fade, Book Scalper, VWAP/OI, Liquidation Absorber).
- Embedded AI Sentinel (sub-second Groq LPU + 10s watchdog with direct native emergency closes).
- Embedded AI Prospector (45s market scanner for top perpetuals).
- WezTerm Institutional Terminal Cockpit (default UI, replaces web dashboard).

Usage:
  python run.py                  # Runs in Paper Trading mode with interactive WezTerm/Terminal Cockpit
  python run.py --wezterm        # Spawns dedicated WezTerm Cockpit window
  python run.py --headless       # Headless background daemon mode (no UI)
  python run.py --live           # Live execution on Testnet burner wallet
  python run.py --web            # Optionally enable legacy web server (http://localhost:8000)
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import os
import time
import signal
import shutil
import argparse
import threading
import subprocess
from rich.console import Console
from rich.panel import Panel

from src.engine.unified_engine import UnifiedEngine
from src.tui.data_provider import set_active_engine as set_tui_engine

console = Console()


def find_wezterm_bin() -> str:
    """Locate WezTerm executable on system."""
    for candidate in [
        shutil.which("wezterm"),
        os.path.expanduser("~/.local/bin/wezterm"),
        "/home/jooshoo/.local/bin/wezterm",
        "/usr/bin/wezterm",
        "/usr/local/bin/wezterm",
    ]:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid Autonomous Multi-Strategy System & WezTerm Cockpit")
    parser.add_argument("--top-n", type=int, default=15, help="Number of perpetual markets to monitor (default 15)")
    parser.add_argument("--live", action="store_true", help="Run in Live Testnet mode with burner wallet instead of local Paper emulator")
    parser.add_argument("--risk-pct", type=float, default=0.01, help="Risk percentage per trade (default 0.01 = 1%%)")
    parser.add_argument("--rr-ratio", type=float, default=2.5, help="Reward-to-risk ratio (default 2.5)")
    parser.add_argument("--watchdog-interval", type=float, default=10.0, help="Active trade watchdog audit interval in seconds (default 10.0)")
    parser.add_argument("--prospector-interval", type=float, default=45.0, help="Market prospector scan interval in seconds (default 45.0s)")

    # UI mode options
    parser.add_argument("--wezterm", action="store_true", help="Launch dedicated WezTerm Cockpit window")
    parser.add_argument("--tui", action="store_true", help="Force interactive terminal cockpit in current session")
    parser.add_argument("--headless", action="store_true", help="Run engine in headless background daemon mode")
    parser.add_argument("--web", action="store_true", help="Optionally start legacy web server at http://localhost:8000")
    parser.add_argument("--port", type=int, default=8000, help="Web Cockpit port if --web is enabled (default 8000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Web Cockpit bind host if --web is enabled")
    args = parser.parse_args()

    paper_mode = not args.live

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

    # 2. Hook engine into TUI Data Provider for zero-latency memory telemetry
    set_tui_engine(engine)

    # 3. Start Trading Engine in background
    engine.start()

    # 4. Optional legacy web server
    if args.web:
        from src.web.server import app, set_active_engine as set_web_engine
        set_web_engine(engine)

        def run_web():
            import uvicorn
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
        console.print(f"[bold cyan]🌐 Legacy Web API running on http://localhost:{args.port}[/bold cyan]")

    # 5. Handle Graceful Shutdown
    def handle_sig(sig, frame):
        console.print("\n[bold yellow]🛑 Shutdown signal received. Stopping all systems cleanly...[/bold yellow]")
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    # 6. UI Launch Routing
    wezterm_bin = find_wezterm_bin()

    if args.wezterm:
        if wezterm_bin and (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")):
            config_file = os.path.join(REPO_ROOT, "wezterm.lua")
            console.print(f"[bold green]🖥️ Spawning WezTerm Cockpit window via {wezterm_bin}...[/bold green]")
            subprocess.Popen([wezterm_bin, "--config-file", config_file, "start", "--cwd", REPO_ROOT])
            # Keep engine thread alive
            try:
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                handle_sig(None, None)
        else:
            console.print("[yellow]⚠️ WezTerm binary or GUI display not available. Falling back to inline TUI...[/yellow]")
            _run_inline_tui(engine)
    elif args.headless or not sys.stdin.isatty():
        banner = Panel.fit(
            f"[bold cyan]HYPERLIQUID UNIFIED AUTONOMOUS TRADING DAEMON[/bold cyan]\n"
            f"• [bold]Execution Mode:[/bold] {'[green]PAPER (Mainnet Data)[/green]' if paper_mode else '[yellow]LIVE (Testnet)[/yellow]'}\n"
            f"• [bold]AI Sentinel:[/bold] [green]ACTIVE[/green] (Groq LPU + Watchdog)\n"
            f"• [bold]Status:[/bold] Headless daemon running. Use [cyan]./launch_wezterm.sh[/cyan] to open cockpit.\n"
            f"[dim]Press Ctrl+C to stop cleanly.[/dim]",
            title="🤖 Antigravity Engine",
            border_style="cyan",
        )
        console.print(banner)
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            handle_sig(None, None)
    else:
        # Default interactive mode: Run full WezTerm Cockpit TUI in current terminal
        _run_inline_tui(engine)


def _run_inline_tui(engine: UnifiedEngine) -> None:
    """Launch full-screen Textual Cockpit connected to the running engine."""
    from src.tui.app import HyperliquidCockpitApp
    app = HyperliquidCockpitApp()
    try:
        app.run()
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
