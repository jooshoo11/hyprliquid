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
from src.scanner.live_ws_feed import live_feed
from src.scanner.neural_l2_scanner import neural_scanner
from src.execution.auto_trader import auto_trader
from src.utils.pixel_ai import pixel_ai
from src.risk.self_improving_engine import self_improving_engine
from src.risk.regime_governor import regime_governor

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

            # 2. Only generate heuristic top prospects if prospects file is missing
            if not prospects_file.exists():
                top_prospects = hl.generate_top_prospects()
                if top_prospects:
                    with open(prospects_file, "w") as f:
                        json.dump({"prospects": top_prospects, "timestamp": time.time(), "engine": "Heuristic_Fallback"}, f, indent=2)
        except Exception:
            pass

        iteration += 1
        # Scan every 45 seconds
        time.sleep(45)


def main():
    port = 8000
    host = "0.0.0.0"
    lan_ip = get_local_ip()
    hw_info = pixel_ai.get_hardware_info()
    adaptive_info = self_improving_engine.get_adaptive_params()

    banner = Panel.fit(
        f"[bold cyan]HYPERLIQUID AI TRADING COCKPIT (PIXEL 9 OPTIMIZED)[/bold cyan]\n\n"
        f"• [bold]Local Cockpit URL:[/bold]    [bold underline cyan]http://localhost:{port}[/bold underline cyan]\n"
        f"• [bold]LAN Network URL:[/bold]      [bold underline green]http://{lan_ip}:{port}[/bold underline green]\n\n"
        f"• [bold]Hardware SoC:[/bold]         Google Tensor G4 (8 Cores: Cortex-X4/A720/A520 + Mali-G715)\n"
        f"• [bold]On-Device AI:[/bold]         [green]{hw_info['engine_status']}[/green] ({hw_info['token_cost']})\n"
        f"• [bold]Self-Improving Engine:[/bold] [green]ACTIVE[/green] (Win Rate: {adaptive_info.get('win_rate_recent', 60.0):.1f}%, TP RR: {adaptive_info.get('tp_rr_ratio', 2.5):.1f}x)\n"
        f"• [bold]Live L2 Streaming:[/bold]   [green]ACTIVE[/green] (WebSocket allMids + L2 Book Depth Walls)\n"
        f"• [bold]Neural L2 Scanner:[/bold]   [green]ACTIVE[/green] (Temporal Microstructure + Hard Mathematical Brackets)\n"
        f"• [bold]Regime Governor:[/bold]     [green]ACTIVE[/green] (Tensor G4 5m Playbook Modulation: Momentum / Chop / Cascade)\n"
        f"• [bold]Auto-Pilot Risk:[/bold]       PortfolioGuard + Devil's Advocate Gatekeeper + Dynamic Max Leverage\n"
        f"[dim]Access from Chrome on this phone or any device on the same Wi-Fi network.[/dim]",
        title="📱 Pixel 9 Autonomous Trading Node",
        border_style="cyan",
    )
    console.print(banner)

    # Start real-time WebSocket L2 streaming feed
    live_feed.start()
    console.print("[bold green]⚡ Real-Time WebSocket L2 Streaming Feed Connected (wss://api.hyperliquid.xyz/ws)[/bold green]")

    # Start Mid-Frequency Market Regime Governor
    regime_governor.start()
    console.print("[bold green]🛡️ On-Device Market Regime Governor Active (Macro Breadth & Playbook Modulation)[/bold green]")

    # Start Onboard Neural L2 Market Scanner (Tensor G4 + Qwen 2.5 1.5B)
    neural_scanner.start()
    console.print("[bold green]🧠 Onboard Tensor G4 Neural L2 Scanner Active (Local AI Conviction & OBI Depth)[/bold green]")

    # Start background intelligence thread (macro sector rotation radar)
    t_intel = threading.Thread(target=background_intelligence_loop, daemon=True, name="HLIntelligence")
    t_intel.start()

    # Start autonomous auto-trader execution & sentinel watchdog daemon
    auto_trader.start()
    console.print("[bold green]🤖 Autonomous Auto-Pilot Engaged (Live WS Mark Prices | Max 3 Pos | Continuous Learning)[/bold green]")

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
