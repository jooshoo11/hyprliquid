"""
Hyperliquid Real-Time WebSocket L2, Tape & Temporal Microstructure Engine (src/scanner/live_ws_feed.py)

Maintains a persistent, low-latency WebSocket connection to wss://api.hyperliquid.xyz/ws:
1. allMids Stream: Real-time price updates for all perpetuals with zero HTTP polling.
2. l2Book Depth Stream: Live 10-level bid/ask order book for active positions & top candidates.
3. Trades Tape Stream: Live public trade executions with taker side ('B' buy vs 'A' sell), size, and price.
4. Temporal Microstructure Dynamics:
   - Cumulative Volume Delta (CVD) across 1m, 5m, and 15m rolling windows.
   - Aggressive Taker Buy/Sell Ratios & Trade Velocity (TPS).
   - CVD Absorption & Divergence Detection (e.g. dumping into passive bids).
   - Order Book Resilience & Replenishment Rate.
   - Open Interest (OI) Delta & Funding Rate Acceleration.
   - Cross-Asset Benchmark Anchoring (BTC/ETH 5m delta, Hyperliquid market breadth).
5. Dense Tabular Feature Vector generator for sub-500ms Pixel 9 Tensor G4 inference.
"""

import os
import sys
import json
import time
import asyncio
import threading
from typing import Dict, Any, List, Optional, Set, Tuple
from collections import deque, defaultdict
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
    Singleton real-time streaming feed managing allMids, L2 depth, trade tape, and temporal dynamics.
    """

    def __init__(self):
        self.prices: Dict[str, float] = {}
        self.l2_books: Dict[str, Dict[str, Any]] = {}
        self.microstructure: Dict[str, Dict[str, Any]] = {}

        # Temporal Trade Tape & Order Flow Buffers
        # trades_history[coin] = deque of (timestamp_sec, side, price, size, notional_usd)
        self.trades_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=2500))
        # price_history[coin] = deque of (timestamp_sec, price)
        self.price_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=900))
        # oi_history[coin] = deque of (timestamp_sec, open_interest, funding_apr)
        self.oi_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=180))

        # Resilience & Book dynamics
        self.resilience_map: Dict[str, str] = {}
        self._last_sweep_time: Dict[str, float] = {}

        # Macro benchmarks
        self.macro_metrics: Dict[str, Any] = {
            "btc_ret_1m": 0.0,
            "btc_ret_5m": 0.0,
            "eth_ret_1m": 0.0,
            "eth_ret_5m": 0.0,
            "hl_breadth_pct": 50.0,
            "hl_breadth_str": "50%_BULL",
        }

        self.subscribed_coins: Set[str] = {"BTC", "ETH", "SOL", "XMR"}
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
        """Dynamically add coin to live L2 order book and trade tape stream."""
        coin = coin.upper()
        with self._lock:
            if coin in self.subscribed_coins:
                return
            self.subscribed_coins.add(coin)

        if self._loop and self._loop.is_running() and self._ws:
            try:
                asyncio.run_coroutine_threadsafe(self._send_subscribe(coin), self._loop)
            except Exception:
                pass

    async def _send_subscribe(self, coin: str) -> None:
        try:
            # Subscribe both to L2 depth and live public trades tape
            await self._ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": coin}}))
            await self._ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}))
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

                    # 2. Subscribe to initial coins (both l2Book and trades)
                    with self._lock:
                        coins_to_sub = list(self.subscribed_coins)
                    for c in coins_to_sub:
                        await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": c}}))
                        await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))

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
                            px = float(v)
                            coin_sym = k.upper()
                            self.prices[coin_sym] = px
                            # Track rolling price history for returns
                            hist = self.price_history[coin_sym]
                            if not hist or (now - hist[-1][0]) >= 2.0:
                                hist.append((now, px))
                        except (ValueError, TypeError):
                            pass

                self._update_macro_benchmarks(now)

            elif channel == "trades":
                trades_list = data if isinstance(data, list) else [data]
                if not trades_list:
                    return

                with self._lock:
                    for t in trades_list:
                        coin = str(t.get("coin", "")).upper()
                        if not coin:
                            continue
                        side = str(t.get("side", "")).upper()  # 'B' (buyer taker) or 'A' (seller taker)
                        px = float(t.get("px", 0.0))
                        sz = float(t.get("sz", 0.0))
                        notional = px * sz
                        t_sec = float(t.get("time", 0.0)) / 1000.0 if t.get("time") else now

                        self.trades_history[coin].append((t_sec, side, px, sz, notional))

                        # Detect liquidity sweeps (> $20k single sweep)
                        if notional > 20_000:
                            self._last_sweep_time[coin] = now

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

                    # Calculate book resilience
                    last_sweep = self._last_sweep_time.get(coin, 0.0)
                    if (now - last_sweep) < 2.0:
                        resilience = "FAST" if spread_bps <= 2.5 else "SLOW_VOID"
                    else:
                        resilience = "FAST" if spread_bps <= 1.8 else ("MODERATE" if spread_bps <= 3.5 else "SLOW_VOID")
                    self.resilience_map[coin] = resilience

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
                        "resilience": resilience,
                        "timestamp": now,
                    }

                    with self._lock:
                        self.l2_books[coin] = data
                        self.microstructure[coin] = metrics

        except Exception:
            pass

    def _update_macro_benchmarks(self, now: float) -> None:
        """Update BTC/ETH 5m returns and Hyperliquid universe breadth."""
        with self._lock:
            # BTC Returns
            btc_hist = self.price_history.get("BTC")
            if btc_hist and len(btc_hist) >= 2:
                cur_btc = btc_hist[-1][1]
                t_5m_target = now - 300.0
                btc_5m_val = cur_btc
                for t, p in btc_hist:
                    if t >= t_5m_target:
                        btc_5m_val = p
                        break
                self.macro_metrics["btc_ret_5m"] = round(((cur_btc - btc_5m_val) / btc_5m_val * 100.0), 3) if btc_5m_val > 0 else 0.0

            # ETH Returns
            eth_hist = self.price_history.get("ETH")
            if eth_hist and len(eth_hist) >= 2:
                cur_eth = eth_hist[-1][1]
                t_5m_target = now - 300.0
                eth_5m_val = cur_eth
                for t, p in eth_hist:
                    if t >= t_5m_target:
                        eth_5m_val = p
                        break
                self.macro_metrics["eth_ret_5m"] = round(((cur_eth - eth_5m_val) / eth_5m_val * 100.0), 3) if eth_5m_val > 0 else 0.0

            # Breadth: % of coins positive over last 15m/rolling window
            green_count = 0
            total_measured = 0
            for c, h in self.price_history.items():
                if len(h) >= 2:
                    p_now = h[-1][1]
                    p_old = h[0][1]
                    if p_old > 0:
                        total_measured += 1
                        if p_now >= p_old:
                            green_count += 1

            if total_measured > 5:
                breadth_pct = (green_count / total_measured) * 100.0
                self.macro_metrics["hl_breadth_pct"] = round(breadth_pct, 1)
                self.macro_metrics["hl_breadth_str"] = f"{int(round(breadth_pct))}%_BULL"

    def get_flow_metrics(self, coin: str) -> Dict[str, Any]:
        """
        Calculate Cumulative Volume Delta (CVD) and Taker Aggression across rolling windows.
        Detects CVD Absorption & Divergence (e.g. smart money dumping into resting bids).
        """
        coin = coin.upper()
        now = time.time()
        t_1m = now - 60.0
        t_5m = now - 300.0
        t_15m = now - 900.0

        with self._lock:
            trades = list(self.trades_history.get(coin, []))
            px_hist = list(self.price_history.get(coin, []))

        buy_vol_1m = 0.0
        sell_vol_1m = 0.0
        buy_vol_5m = 0.0
        sell_vol_5m = 0.0
        buy_vol_15m = 0.0
        sell_vol_15m = 0.0
        count_1m = 0

        for t_sec, side, px, sz, notional in trades:
            if t_sec >= t_15m:
                if side == "B":
                    buy_vol_15m += notional
                else:
                    sell_vol_15m += notional

                if t_sec >= t_5m:
                    if side == "B":
                        buy_vol_5m += notional
                    else:
                        sell_vol_5m += notional

                    if t_sec >= t_1m:
                        count_1m += 1
                        if side == "B":
                            buy_vol_1m += notional
                        else:
                            sell_vol_1m += notional

        cvd_1m = round(buy_vol_1m - sell_vol_1m, 2)
        cvd_5m = round(buy_vol_5m - sell_vol_5m, 2)
        cvd_15m = round(buy_vol_15m - sell_vol_15m, 2)

        total_5m = buy_vol_5m + sell_vol_5m
        taker_buy_ratio_5m = round(buy_vol_5m / max(1.0, total_5m), 3)
        trade_velocity_tps = round(count_1m / 60.0, 2)

        # 5m Price Delta for Divergence Calculation
        cur_px = self.get_price(coin)
        px_5m_ago = cur_px
        for t, p in px_hist:
            if t >= t_5m:
                px_5m_ago = p
                break
        px_chg_5m = ((cur_px - px_5m_ago) / px_5m_ago * 100.0) if px_5m_ago > 0 else 0.0

        # CVD Divergence Classification
        # Threshold: $10k or 0.15% price delta
        if px_chg_5m >= 0.15 and cvd_5m < -15_000:
            divergence = "BEARISH_ABSORPTION"  # Price rising/flat but heavy aggressive selling -> trap!
        elif px_chg_5m <= -0.15 and cvd_5m > +15_000:
            divergence = "BULLISH_ABSORPTION"  # Price falling/flat but heavy aggressive buying -> trap!
        elif px_chg_5m >= 0.15 and cvd_5m > +15_000:
            divergence = "CONVERGENT_BULL"
        elif px_chg_5m <= -0.15 and cvd_5m < -15_000:
            divergence = "CONVERGENT_BEAR"
        else:
            divergence = "BALANCED"

        resilience = self.resilience_map.get(coin, "FAST")

        return {
            "coin": coin,
            "cvd_1m": cvd_1m,
            "cvd_5m": cvd_5m,
            "cvd_15m": cvd_15m,
            "taker_buy_ratio_5m": taker_buy_ratio_5m,
            "trade_velocity_tps": trade_velocity_tps,
            "px_chg_5m": round(px_chg_5m, 2),
            "divergence": divergence,
            "resilience": resilience,
        }

    def record_asset_context(self, coin: str, open_interest: float, funding_apr: float) -> None:
        """Record open interest and funding rate history for velocity and acceleration tracking."""
        coin = coin.upper()
        now = time.time()
        with self._lock:
            self.oi_history[coin].append((now, float(open_interest), float(funding_apr)))

    def get_derivatives_positioning(self, coin: str) -> Dict[str, Any]:
        """
        Calculate Open Interest Delta and Funding Acceleration.
        Classifies institutional regime: NEW_LONGS, SHORT_SQUEEZE, LONG_LIQUIDATION, NEW_SHORTS.
        """
        coin = coin.upper()
        now = time.time()
        with self._lock:
            hist = list(self.oi_history.get(coin, []))

        if not hist:
            return {
                "oi_delta_15m_pct": 0.0,
                "funding_accel_1h": 0.0,
                "oi_regime": "NEUTRAL_CHOP",
            }

        cur_oi = hist[-1][1]
        cur_fund = hist[-1][2]

        # 15m OI target
        t_15m_target = now - 900.0
        oi_15m_old = cur_oi
        for t, oi, _ in hist:
            if t >= t_15m_target:
                oi_15m_old = oi
                break
        oi_delta_15m = round(((cur_oi - oi_15m_old) / oi_15m_old * 100.0), 2) if oi_15m_old > 0 else 0.0

        # 1h Funding Acceleration target
        t_1h_target = now - 3600.0
        fund_1h_old = cur_fund
        for t, _, fund in hist:
            if t >= t_1h_target:
                fund_1h_old = fund
                break
        funding_accel = round(cur_fund - fund_1h_old, 1)

        # Positioning classification based on Price direction + OI change
        flow = self.get_flow_metrics(coin)
        px_chg_5m = flow.get("px_chg_5m", 0.0)

        if px_chg_5m >= 0.20 and oi_delta_15m >= 1.0:
            oi_regime = "NEW_LONGS"  # Aggressive new long leverage entering
        elif px_chg_5m >= 0.20 and oi_delta_15m <= -1.0:
            oi_regime = "SHORT_SQUEEZE"  # Short covering / squeeze exhaustion
        elif px_chg_5m <= -0.20 and oi_delta_15m <= -1.0:
            oi_regime = "LONG_LIQUIDATION"  # Long flush cascade
        elif px_chg_5m <= -0.20 and oi_delta_15m >= 1.0:
            oi_regime = "NEW_SHORTS"  # Aggressive short expansion
        else:
            oi_regime = "NEUTRAL_CHOP"

        return {
            "oi_delta_15m_pct": oi_delta_15m,
            "funding_accel_1h": funding_accel,
            "oi_regime": oi_regime,
        }

    def get_compact_feature_vector(self, coin: str, leverage: float = 10.0, strategy: str = "Auto") -> str:
        """
        Generate the ultra-dense, tabular feature vector for sub-500ms Pixel 9 Tensor G4 inference:
        [TICKER] SUI | PX: 1.842 | SPRD: 1.2bps | LEV: 10x
        [FLOW] OBI: +0.28 | CVD_5M: +$420k | TAKER_BUY_RATIO: 0.64 | RESILIENCE: FAST
        [DERIV] FUND_APR: -34.2% (ACCEL: -12%/h) | OI_15M: +6.8% (SHORT_SQUEEZE)
        [MACRO] BTC_5M: +0.12% | ETH_5M: +0.08% | HL_BREADTH: 68%_BULL
        """
        coin = coin.upper()
        px = self.get_price(coin)
        micro = self.get_microstructure(coin) or {}
        flow = self.get_flow_metrics(coin)
        deriv = self.get_derivatives_positioning(coin)

        spread = float(micro.get("spread_bps", 1.0))
        obi = float(micro.get("obi", 0.0))
        resilience = flow.get("resilience", "FAST")
        cvd_5m = flow.get("cvd_5m", 0.0)

        # Format CVD nicely
        if abs(cvd_5m) >= 1_000_000:
            cvd_str = f"{'+' if cvd_5m >= 0 else ''}${cvd_5m / 1e6:.1f}M"
        elif abs(cvd_5m) >= 1_000:
            cvd_str = f"{'+' if cvd_5m >= 0 else ''}${cvd_5m / 1e3:.0f}k"
        else:
            cvd_str = f"{'+' if cvd_5m >= 0 else ''}${cvd_5m:.0f}"

        taker_ratio = flow.get("taker_buy_ratio_5m", 0.5)

        # Funding rate
        fund_apr = 0.0
        with self._lock:
            hist = self.oi_history.get(coin)
            if hist:
                fund_apr = hist[-1][2]
        accel = deriv.get("funding_accel_1h", 0.0)
        oi_delta = deriv.get("oi_delta_15m_pct", 0.0)
        oi_regime = deriv.get("oi_regime", "NEUTRAL_CHOP")

        btc_5m = self.macro_metrics.get("btc_ret_5m", 0.0)
        eth_5m = self.macro_metrics.get("eth_ret_5m", 0.0)
        breadth = self.macro_metrics.get("hl_breadth_str", "50%_BULL")

        vector = (
            f"[TICKER] {coin} | PX: {px:,.4f} | SPRD: {spread:.1f}bps | LEV: {leverage:.0f}x\n"
            f"[FLOW] OBI: {obi:+.2f} | CVD_5M: {cvd_str} | TAKER_BUY_RATIO: {taker_ratio:.2f} | RESILIENCE: {resilience}\n"
            f"[DERIV] FUND_APR: {fund_apr:+.1f}% (ACCEL: {accel:+.1f}%/h) | OI_15M: {oi_delta:+.1f}% ({oi_regime})\n"
            f"[MACRO] BTC_5M: {btc_5m:+.2f}% | ETH_5M: {eth_5m:+.2f}% | HL_BREADTH: {breadth}"
        )
        return vector

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
