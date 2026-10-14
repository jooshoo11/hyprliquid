"""
Analytics Pipeline & Dashboard Bridge (src/scanner/analytics_pipeline.py)
Directly pipes real-time microstructure, depth decay, trade sweeps, liquidations,
and market regime analytics from the Hyperliquid Analytics Dashboard (ws://localhost:8001/ws/analytics)
into the Nautilus trading engine, AI Sentinel watchdog, and risk manager.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
import websocket

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
os.makedirs(BRIDGE_DIR, exist_ok=True)

logger = logging.getLogger("AnalyticsPipeline")


@dataclass
class PositionMicrostructureAudit:
    coin: str
    side: str
    pnl_pct: float
    is_toxic: bool
    toxic_reasons: List[str] = field(default_factory=list)
    bid_decay_pct: float = 0.0
    ask_decay_pct: float = 0.0
    l5_imbalance: float = 0.0
    sweep_direction: str = "FLAT"
    liquidation_status: str = "NORMAL"
    market_regime: str = "NORMAL"
    crowding_flag: str = "NEUTRAL"
    details: Dict[str, Any] = field(default_factory=dict)

MicrostructureAudit = PositionMicrostructureAudit


class AnalyticsPipeline:
    """
    Real-time client streaming market microstructure analytics from the
    local Hyperliquid Analytics Dashboard (FastAPI / WebSocket).
    """

    def __init__(
        self,
        dashboard_host: str = "127.0.0.1",
        dashboard_port: int = 8001,
        auto_start: bool = True,
        coins: Optional[List[str]] = None,
    ):
        self.dashboard_host = dashboard_host
        self.dashboard_port = dashboard_port
        self.base_ws_url = f"ws://{dashboard_host}:{dashboard_port}/ws/analytics"
        self.base_api_url = f"http://{dashboard_host}:{dashboard_port}/api/analytics"
        self.bridge_file = os.path.join(BRIDGE_DIR, "analytics_microstructure.json")

        self._lock = threading.RLock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._sockets: Dict[str, websocket.WebSocketApp] = {}
        self._running = True
        self._last_persist_time: float = 0.0

        initial_coins = coins or ["SOL", "BTC", "ETH"]
        if auto_start:
            for coin in initial_coins:
                self.subscribe_coin(coin)

    def is_dashboard_online(self) -> bool:
        """Check if dashboard backend is reachable via REST."""
        for host in [self.dashboard_host, "127.0.0.1", "localhost"]:
            try:
                r = requests.get(f"http://{host}:{self.dashboard_port}/", timeout=1.5)
                if r.status_code == 200:
                    return True
            except Exception:
                continue
        return False

    def subscribe_coin(self, coin: str) -> None:
        """Launch background WebSocket stream thread for specified perpetual coin."""
        coin_clean = coin.upper().split("-")[0].split(".")[0].strip()
        with self._lock:
            if coin_clean in self._threads and self._threads[coin_clean].is_alive():
                return

            def on_message(ws, message):
                try:
                    data = json.loads(message)
                    with self._lock:
                        self._cache[coin_clean] = {
                            "timestamp": time.time(),
                            "coin": coin_clean,
                            "data": data,
                        }
                    self._persist_bridge()
                except Exception as err:
                    logger.debug(f"Error parsing message for {coin_clean}: {err}")

            def on_error(ws, error):
                logger.debug(f"Analytics WS error for {coin_clean}: {error}")

            def on_close(ws, close_status_code, close_msg):
                logger.debug(f"Analytics WS closed for {coin_clean}")

            def run():
                while self._running:
                    try:
                        url = f"{self.base_ws_url}?coin={coin_clean}"
                        ws = websocket.WebSocketApp(
                            url,
                            on_message=on_message,
                            on_error=on_error,
                            on_close=on_close,
                        )
                        self._sockets[coin_clean] = ws
                        ws.run_forever(ping_interval=20, ping_timeout=10)
                    except Exception as loop_err:
                        logger.debug(f"Analytics stream reconnect error for {coin_clean}: {loop_err}")
                    time.sleep(3.0)

            t = threading.Thread(target=run, daemon=True, name=f"AnalyticsPipeline-{coin_clean}")
            self._threads[coin_clean] = t
            t.start()

    def get_latest(self, coin: str) -> Optional[Dict[str, Any]]:
        """Return cached analytics snapshot for coin (auto-subscribes if missing)."""
        coin_clean = coin.upper().split("-")[0].split(".")[0].strip()
        with self._lock:
            if coin_clean not in self._cache:
                self.subscribe_coin(coin_clean)
                return None
            entry = self._cache.get(coin_clean)
            if entry and (time.time() - entry.get("timestamp", 0)) < 15.0:
                return entry.get("data")
            return None

    def _persist_bridge(self, force: bool = False) -> None:
        """Write latest market microstructure snapshot atomically to bridge/ (throttled to 1.0s)."""
        now = time.time()
        try:
            with self._lock:
                if not force and (now - self._last_persist_time < 1.0):
                    return
                self._last_persist_time = now
                export_data = {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "coins": {c: v.get("data") for c, v in self._cache.items()},
                }
            tmp_path = self.bridge_file + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(export_data, f, indent=2)
            os.replace(tmp_path, self.bridge_file)
        except Exception:
            pass

    def audit_position_risk(
        self,
        coin: str,
        side: str,
        pnl_pct: float,
        hold_time_seconds: float = 60.0,
    ) -> PositionMicrostructureAudit:
        """
        Evaluate real-time microstructure health for an active position.
        Identifies orderbook decay, aggressive trade sweep reversals, liquidation bursts,
        and crowded positioning before a full stop-loss is triggered.
        """
        data = self.get_latest(coin)
        side_upper = side.upper()

        audit = PositionMicrostructureAudit(
            coin=coin,
            side=side_upper,
            pnl_pct=pnl_pct,
            is_toxic=False,
        )

        if not data:
            return audit

        audit.details = data

        # Microstructure Data Quality Gate
        coin_data = data.get("coins", {}).get(coin, {}) if isinstance(data, dict) and isinstance(data.get("coins"), dict) else {}
        has_coin_sys = "system_status" in coin_data if isinstance(coin_data, dict) else False
        has_root_sys = "system_status" in data if isinstance(data, dict) else False

        if has_coin_sys or has_root_sys:
            coin_sys = coin_data.get("system_status", data.get("system_status", {})) if isinstance(coin_data, dict) else (data.get("system_status", {}) if isinstance(data, dict) else {})
            if not isinstance(coin_sys, dict):
                coin_sys = {}
            modules = coin_sys.get("modules") or {}
            ob_module = modules.get("orderbook") or {}

            if (
                not coin_sys.get("data_quality_ok", False)
                or not coin_sys.get("feed_connected", False)
                or not ob_module.get("fresh", False)
            ):
                return audit

        # 1. Depth Decay Check (order book wall deterioration)
        depth_decay = data.get("depth_decay", {})
        bid_decay = float(depth_decay.get("bid_decay_percent") or depth_decay.get("bid_decay_pct") or 0.0)
        ask_decay = float(depth_decay.get("ask_decay_percent") or depth_decay.get("ask_decay_pct") or 0.0)
        bid_status = str(depth_decay.get("bid_status", "OK")).upper()
        ask_status = str(depth_decay.get("ask_status", "OK")).upper()

        audit.bid_decay_pct = bid_decay
        audit.ask_decay_pct = ask_decay

        # If LONG and bid support rapidly decaying while in negative PnL
        if side_upper == "LONG" and (bid_status == "CRITICAL" or bid_decay > 50.0) and pnl_pct < -0.4:
            audit.is_toxic = True
            audit.toxic_reasons.append(
                f"Bid depth support evaporating (Bid Decay: {bid_decay:+.1f}%, Status: {bid_status})"
            )

        # If SHORT and ask resistance rapidly decaying while in negative PnL
        if side_upper == "SHORT" and (ask_status == "CRITICAL" or ask_decay > 50.0) and pnl_pct < -0.4:
            audit.is_toxic = True
            audit.toxic_reasons.append(
                f"Ask depth resistance evaporating (Ask Decay: {ask_decay:+.1f}%, Status: {ask_status})"
            )

        # 2. Aggressive Trade Sweep Reversal Check
        trade_flow = data.get("trade_flow", {})
        sweep_dir = str(trade_flow.get("sweep_direction", "")).upper()
        buy_ratio = float(trade_flow.get("buy_ratio", 0.5))
        audit.sweep_direction = sweep_dir

        # buy_ratio is 0.0 to 1.0 (<=0.35 means >=65% sell volume)
        if side_upper == "LONG" and ("DOWN" in sweep_dir) and buy_ratio <= 0.35 and pnl_pct < -0.5:
            audit.is_toxic = True
            audit.toxic_reasons.append(
                f"Aggressive sell sweeps detected (Sweep: {sweep_dir}, Buy Ratio: {buy_ratio*100:.1f}%)"
            )

        # (>=0.65 means >=65% buy volume)
        if side_upper == "SHORT" and ("UP" in sweep_dir) and buy_ratio >= 0.65 and pnl_pct < -0.5:
            audit.is_toxic = True
            audit.toxic_reasons.append(
                f"Aggressive buy sweeps detected (Sweep: {sweep_dir}, Buy Ratio: {buy_ratio*100:.1f}%)"
            )

        # 3. Liquidation Cascade Hazard Check
        liq = data.get("liquidations", {})
        liq_status = str(liq.get("status", "NORMAL")).upper()
        audit.liquidation_status = liq_status
        short_liqs = int(liq.get("short_liquidations", 0))
        long_liqs = int(liq.get("long_liquidations", 0))

        if side_upper == "SHORT" and short_liqs >= 3 and liq_status in ("ELEVATED", "BURST"):
            audit.is_toxic = True
            audit.toxic_reasons.append(
                f"Short liquidation cascade triggering against position ({short_liqs} short liqs)"
            )

        if side_upper == "LONG" and long_liqs >= 3 and liq_status in ("ELEVATED", "BURST"):
            audit.is_toxic = True
            audit.toxic_reasons.append(
                f"Long liquidation cascade triggering against position ({long_liqs} long liqs)"
            )

        # 4. Position Crowding Squeeze Warning
        crowding = data.get("crowding", {})
        is_crowded_long = crowding.get("is_crowded_long", False)
        is_crowded_short = crowding.get("is_crowded_short", False)

        if side_upper == "LONG" and is_crowded_long and pnl_pct < -0.6:
            audit.crowding_flag = "CROWDED_LONG"
            audit.is_toxic = True
            audit.toxic_reasons.append("Crowded long positions unwinding under adverse carry")

        if side_upper == "SHORT" and is_crowded_short and pnl_pct < -0.6:
            audit.crowding_flag = "CROWDED_SHORT"
            audit.is_toxic = True
            audit.toxic_reasons.append("Crowded short positions unwinding under short squeeze")

        # 5. Market Regime Context
        regime = data.get("regime", {})
        mkt_regime = str(regime.get("market_regime", "NORMAL")).upper()
        audit.market_regime = mkt_regime

        if mkt_regime in ("CRASH", "LIQUIDATION_EVENT") and pnl_pct < -0.5:
            audit.is_toxic = True
            audit.toxic_reasons.append(f"Severe market dislocation regime active: {mkt_regime}")

        return audit

    def get_entry_signal_filter(self, coin: str, side: str) -> Tuple[bool, str]:
        """
        Filters new strategy entries against live microstructure and order book health.
        Returns: (is_allowed, reason)
        """
        data = self.get_latest(coin)
        if not data:
            return True, "No dashboard data (permissive default)"

        # Microstructure Data Quality Gate
        coin_data = data.get("coins", {}).get(coin, {}) if isinstance(data, dict) and isinstance(data.get("coins"), dict) else {}
        has_coin_sys = "system_status" in coin_data if isinstance(coin_data, dict) else False
        has_root_sys = "system_status" in data if isinstance(data, dict) else False

        if has_coin_sys or has_root_sys:
            coin_sys = coin_data.get("system_status", data.get("system_status", {})) if isinstance(coin_data, dict) else (data.get("system_status", {}) if isinstance(data, dict) else {})
            if not isinstance(coin_sys, dict):
                coin_sys = {}
            modules = coin_sys.get("modules") or {}
            ob_module = modules.get("orderbook") or {}

            if (
                not coin_sys.get("data_quality_ok", False)
                or not coin_sys.get("feed_connected", False)
                or not ob_module.get("fresh", False)
            ):
                return True, "Analytics feed offline - permissive default"

        side_upper = side.upper()

        # Reject if liquidity regime is THIN
        regime = data.get("regime", {})
        liq_regime = str(regime.get("liquidity_regime", "NORMAL")).upper()
        if liq_regime == "THIN":
            return False, f"Orderbook depth too thin for safe execution (Liquidity Regime: THIN)"

        # Check L5 imbalance alignment
        ob = data.get("orderbook", {})
        imbalance = ob.get("imbalance", {})
        l5_imb = float(imbalance.get("l5", 0.0))

        if side_upper == "LONG" and l5_imb < -0.45:
            return False, f"Severe adverse orderbook imbalance: L5 Imbalance {l5_imb*100:+.1f}% (heavy asks)"

        if side_upper == "SHORT" and l5_imb > 0.45:
            return False, f"Severe adverse orderbook imbalance: L5 Imbalance {l5_imb*100:+.1f}% (heavy bids)"

        # Check crowding
        crowding = data.get("crowding", {})
        if side_upper == "LONG" and crowding.get("is_crowded_long", False):
            return False, "Market already heavily crowded LONG (high flush risk)"

        if side_upper == "SHORT" and crowding.get("is_crowded_short", False):
            return False, "Market already heavily crowded SHORT (short squeeze risk)"

        return True, "Microstructure conditions favorable"

    def stop(self) -> None:
        """Close all streaming WebSocket connections."""
        self._running = False
        with self._lock:
            for ws in self._sockets.values():
                try:
                    ws.close()
                except Exception:
                    pass
