"""
Hyperliquid Real-Time WebSocket L2 & Price Streaming Engine (src/scanner/live_ws_feed.py)

Maintains a persistent, low-latency WebSocket connection to wss://api.hyperliquid.xyz/ws:
1. allMids Stream: Real-time price updates for all perpetuals with zero HTTP polling.
2. l2Book Depth Stream: Live 10-level bid/ask order book for active positions & top candidates.
3. Microstructure Analytics:
   - Order Book Imbalance (OBI) from -1.0 (sell heavy) to +1.0 (buy heavy).
   - Volume-Weighted Micro-Price (fair value estimation).
   - Bid-Ask Spread in Basis Points (BPS).
   - Depth wall thickness & collapse detection.
"""

import os
import sys
import json
import time
import asyncio
import threading
from typing import Dict, Any, List, Optional, Set
from pathlib import Path

import websockets

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.scanner.microstructure import (
    calculate_micro_price,
    calculate_order_book_imbalance,
    calculate_spread_bps,
)

WS_URL = "wss://api.hyperliquid.xyz/ws"


class LiveHyperliquidFeed:
    """
    Singleton real-time streaming feed managing allMids and dynamic L2 order book subscriptions.
    """

    def __init__(self):
        self.prices: Dict[str, float] = {}
        self.l2_books: Dict[str, Dict[str, Any]] = {}
        self.microstructure: Dict[str, Dict[str, Any]] = {}

        self.subscribed_coins: Set[str] = {"BTC", "ETH", "SOL"}
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws = None
        self.last_update_time: float = 0.0

    def get_price(self, coin: str) -> float:
        """Get the latest streaming mark price (zero latency, zero HTTP)."""
        with self._lock:
            return self.prices.get(coin.upper(), 0.0)

    def get_all_prices(self) -> Dict[str, float]:
        """Get snapshot of all streaming mid prices."""
        with self._lock:
            return dict(self.prices)

    def get_l2_book(self, coin: str) -> Optional[Dict[str, Any]]:
        """Get latest L2 order book snapshot for coin."""
        with self._lock:
            return self.l2_books.get(coin.upper())

    def get_microstructure(self, coin: str) -> Optional[Dict[str, Any]]:
        """Get real-time OBI, micro-price, and spread metrics."""
        with self._lock:
            return self.microstructure.get(coin.upper())

    def subscribe_coin(self, coin: str) -> None:
        """Dynamically add coin to live L2 order book stream."""
        coin = coin.upper()
        with self._lock:
            if coin in self.subscribed_coins:
                return
            self.subscribed_coins.add(coin)

        if self._loop and self._ws and not self._ws.closed:
            asyncio.run_coroutine_threadsafe(self._send_subscribe(coin), self._loop)

    async def _send_subscribe(self, coin: str) -> None:
        try:
            msg = {"method": "subscribe", "subscription": {"type": "l2Book", "coin": coin}}
            await self._ws.send(json.dumps(msg))
        except Exception:
            pass

    async def _run(self) -> None:
        """Resilient background WebSocket loop with automatic reconnect."""
        while self._running:
            try:
                async with websockets.connect(
                    WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    # 1. Subscribe to allMids
                    await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "allMids"}}))

                    # 2. Subscribe to initial L2 book coins
                    with self._lock:
                        coins_to_sub = list(self.subscribed_coins)
                    for c in coins_to_sub:
                        await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": c}}))

                    while self._running:
                        raw = await ws.recv()
                        self._process_message(raw)
            except Exception:
                await asyncio.sleep(2.0)

    def _process_message(self, raw_str: str) -> None:
        try:
            msg = json.loads(raw_str)
            channel = msg.get("channel")
            data = msg.get("data")
            if not channel or not data:
                return

            now = time.time()
            self.last_update_time = now

            if channel == "allMids":
                mids = data.get("mids", {})
                with self._lock:
                    for k, v in mids.items():
                        try:
                            self.prices[k.upper()] = float(v)
                        except (ValueError, TypeError):
                            pass

            elif channel == "l2Book":
                coin = data.get("coin", "").upper()
                levels = data.get("levels", [[], []])
                bids = levels[0] if len(levels) > 0 else []
                asks = levels[1] if len(levels) > 1 else []

                if bids and asks:
                    best_bid = float(bids[0].get("px", 0.0))
                    bid_sz = float(bids[0].get("sz", 0.0))
                    best_ask = float(asks[0].get("px", 0.0))
                    ask_sz = float(asks[0].get("sz", 0.0))

                    obi = calculate_order_book_imbalance(bids, asks, depth=5)
                    micro_px = calculate_micro_price(best_bid, best_ask, bid_sz, ask_sz)
                    spread_bps = calculate_spread_bps(best_bid, best_ask)

                    bid_depth_usd = sum(float(b.get("px", 0.0)) * float(b.get("sz", 0.0)) for b in bids[:10])
                    ask_depth_usd = sum(float(a.get("px", 0.0)) * float(a.get("sz", 0.0)) for a in asks[:10])

                    metrics = {
                        "coin": coin,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "micro_price": round(micro_px, 4),
                        "obi": round(obi, 3),
                        "spread_bps": round(spread_bps, 2),
                        "bid_depth_usd": round(bid_depth_usd, 2),
                        "ask_depth_usd": round(ask_depth_usd, 2),
                        "imbalance_label": "BID_HEAVY 🟢" if obi > 0.25 else ("ASK_HEAVY 🔴" if obi < -0.25 else "BALANCED ⚖️"),
                        "timestamp": now,
                    }

                    with self._lock:
                        self.l2_books[coin] = data
                        self.microstructure[coin] = metrics

        except Exception:
            pass

    def start(self) -> None:
        """Start streaming thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True

        def _thread_target():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run())

        self._thread = threading.Thread(target=_thread_target, daemon=True, name="LiveWSFeed")
        self._thread.start()

    def stop(self) -> None:
        """Stop streaming thread."""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)


# Shared singleton instance
live_feed = LiveHyperliquidFeed()
