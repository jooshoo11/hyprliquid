#!/usr/bin/env python3
"""
Hyperliquid Mobile Web Cockpit & Intelligence Service (run_cockpit.py)
High-performance standalone runner designed for Termux / Android environment:
- Runs FastAPI + WebSocket Web Cockpit on 0.0.0.0:8000 (accessible across local Wi-Fi).
- Background surveillance loop streaming 234 Hyperliquid perp market metrics,
  sector momentum rotations, extreme negative funding short squeeze alerts,
  and dual-confirmation signals from Crypto_Sonar.
"""

import os
import sys
import time
import json
import socket
import threading
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import uvicorn
from rich.console import Console
from rich.panel import Panel

from src.scanner.hl_intelligence import HyperliquidIntelligence
from src.scanner.sonar_bridge import SonarBridge
from src.execution.auto_trader import auto_trader

console = Console()


def get_local_ip() -> str:
    """Detect the active Wi-Fi LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def background_intelligence_loop():
    """Periodically scans Hyperliquid perps & updates bridge/market_regime.json and bridge/prospects.json."""
    hl = HyperliquidIntelligence()
    bridge_dir = Path(REPO_ROOT) / "bridge"
    bridge_dir.mkdir(exist_ok=True)
    regime_file = bridge_dir / "market_regime.json"
    prospects_file = bridge_dir / "prospects.json"

    # Initial brief sleep to let server bind
    time.sleep(2)

    iteration = 0
    while True:
        try:
            # 1. Update Sector Radar & Squeeze Alerts every 45s
            intel = hl.analyze_sector_momentum()
            if intel.get("status") == "success":
                regime_data = {
                    "regime": "MOMENTUM_EXPANSION" if intel.get("hottest_sector") else "SIDEWAYS_CHOP",
                    "hottest_sector": intel.get("hottest_sector", "MEMES"),
                    "active_perpetuals": intel.get("active_perpetuals", 234),
                    "new_listings": intel.get("new_listings", []),
                    "squeeze_alerts": intel.get("squeeze_alerts", []),
                    "sectors": intel.get("sectors", {}),
                    "timestamp": time.time(),
                }
                with open(regime_file, "w") as f:
                    json.dump(regime_data, f, indent=2)

            # 2. Update Top 10 Ranked Prospects autonomously on startup and every ~3.5 minutes
            if iteration % 5 == 0:
                top_prospects = hl.generate_top_prospects()
                if top_prospects:
                    with open(prospects_file, "w") as f:
                        json.dump({"prospects": top_prospects, "timestamp": time.time()}, f, indent=2)
        except Exception:
            pass

        iteration += 1
        # Scan every 45 seconds
        time.sleep(45)


def main():
    port = 8000
    host = "0.0.0.0"
    lan_ip = get_local_ip()

    banner = Panel.fit(
        f"[bold cyan]HYPERLIQUID AI TRADING COCKPIT (MOBILE DAEMON)[/bold cyan]\n\n"
        f"• [bold]Local Device URL:[/bold]   [bold underline cyan]http://localhost:{port}[/bold underline cyan]\n"
        f"• [bold]LAN Network URL:[/bold]    [bold underline green]http://{lan_ip}:{port}[/bold underline green]\n\n"
        f"• [bold]Surveillance:[/bold]        234+ Hyperliquid Perps + Sector Radar\n"
        f"• [bold]Short Squeeze Alerts:[/bold] Extreme negative funding (< -15% APR)\n"
        f"• [bold]Status:[/bold]              Streaming live over WebSocket (/ws)\n"
        f"[dim]Access the LAN Network URL from any laptop, tablet, or phone on the same Wi-Fi.[/dim]",
        title="📱 Web Cockpit Online",
        border_style="green",
    )
    console.print(banner)

    # Start background intelligence thread
    t_intel = threading.Thread(target=background_intelligence_loop, daemon=True, name="HLIntelligence")
    t_intel.start()

    # Start autonomous auto-trader execution & sentinel watchdog daemon
    auto_trader.start()
    console.print("[bold green]🤖 Autonomous Auto-Pilot Daemon started (Watchdog 5s | Entry 15s | Max 3 Pos)[/bold green]")

    # Run uvicorn server directly
    uvicorn.run(
        "src.web.server:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
