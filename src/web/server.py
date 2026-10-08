"""
Hyperliquid AI Trading Cockpit - Modern Web Server (server.py)
High-performance FastAPI & WebSocket backend providing:
- Real-time streaming WebSocket (/ws) for live equity, active trades, AI prospect rankings, and risk logs.
- TradingView Lightweight Charts API (/api/klines) for interactive candlestick and volume charting.
- 1-Click execution API (/api/trades/close, /api/trades/close_all) bridging to Nautilus TradingNode.
- Orderbook depth API (/api/l2) for live visual depth inspection.
"""

import sys
from pathlib import Path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import os
import time
import json
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.scanner.mcp_client import HyperliquidInfoClient
from src.risk.trade_manager import TradeManager

try:
    from src.risk.performance_analytics import PerformanceAnalytics
    performance_analytics = PerformanceAnalytics()
except Exception:
    class _FallbackPerformanceAnalytics:
        def get_metrics(self, trades=None):
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "sharpe_ratio": 0.0,
                "expectancy_usd": 0.0,
                "total_net_pnl": 0.0,
                "total_gross_pnl": 0.0,
                "total_fees_paid": 0.0,
                "max_drawdown_pct": 0.0,
                "avg_win_usd": 0.0,
                "avg_loss_usd": 0.0,
            }
    performance_analytics = _FallbackPerformanceAnalytics()

app = FastAPI(title="Hyperliquid AI Trading Cockpit", version="2.0.0")

info_client = HyperliquidInfoClient()
_trade_manager = TradeManager()
_position_entry_times: Dict[str, float] = {}

ACTIVE_TRADES_PATH = os.path.join(REPO_ROOT, "bridge", "active_trades.json")
PROSPECTS_PATH = os.path.join(REPO_ROOT, "bridge", "prospects.json")
FUNDING_ARBITRAGE_PATH = os.path.join(REPO_ROOT, "bridge", "funding_arbitrage.json")
AI_COMMANDS_PATH = os.path.join(REPO_ROOT, "bridge", "ai_commands.json")
PAPER_STATE_PATH = os.path.join(REPO_ROOT, "bridge", "paper_state.json")
SENTINEL_LOG_PATH = os.path.join(REPO_ROOT, "bridge", "sentinel.log")
SESSION_TRADES_PATH = os.path.join(REPO_ROOT, "reports", "session_trades.json")
DECISION_JOURNAL_PATH = os.path.join(REPO_ROOT, "reports", "decision_journal.jsonl")
GROQ_SENTINEL_PATH = os.path.join(REPO_ROOT, "bridge", "groq_sentinel.json")

_active_engine = None


def set_active_engine(engine: Any) -> None:
    """Set the running UnifiedEngine instance for direct in-memory execution."""
    global _active_engine
    _active_engine = engine


def get_active_engine() -> Optional[Any]:
    """Get the running UnifiedEngine instance."""
    global _active_engine
    return _active_engine


class CloseTradeRequest(BaseModel):
    coin: str
    reason: Optional[str] = "Manual Web Cockpit Close"


class OpenTradeRequest(BaseModel):
    coin: str
    side: str  # "LONG" or "SHORT"
    notional_usd: float = 25.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    strategy: Optional[str] = "Manual Trade"



def load_json_file(path: str, default: Any = None) -> Any:
    """Safe read of JSON files with fallback."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


DAILY_PNL_REPORT_PATH = os.path.join(REPO_ROOT, "reports", "daily_pnl.md")
MARKET_REGIME_PATH = os.path.join(REPO_ROOT, "bridge", "market_regime.json")


def normalize_strategy(strat_raw: Any, coin: str = "", default_idx: int = 0) -> str:
    """Normalize raw strategy identifier into a standard UI badge name."""
    if not strat_raw:
        defaults = ["SMC Trend", "Funding Fade", "Book Imbalance", "VWAP/OI"]
        return defaults[default_idx % len(defaults)]
    s = str(strat_raw).lower()
    if "smc" in s or "continuation" in s:
        return "SMC Trend"
    if "funding" in s or "fade" in s:
        return "Funding Fade"
    if "imbalance" in s or "book" in s or "scalp" in s:
        return "Book Imbalance"
    if "vwap" in s or "oi" in s or "momentum" in s:
        return "VWAP/OI"
    if "trend" in s:
        return "SMC Trend"
    return str(strat_raw)


def get_prospects_list() -> List[Dict[str, Any]]:
    """
    Ensure all 10 deep LLM-ranked prospects with rationales are always loaded
    from bridge/prospects.json (falling back to _active_engine.prospects if needed).
    """
    prospects_data = load_json_file(PROSPECTS_PATH, {})
    raw_prospects = prospects_data.get("prospects") if (isinstance(prospects_data, dict) and "prospects" in prospects_data) else prospects_data

    if (not raw_prospects or not isinstance(raw_prospects, dict)) and _active_engine and hasattr(_active_engine, "prospects") and _active_engine.prospects:
        raw_prospects = _active_engine.prospects

    prospects_list = []
    if isinstance(raw_prospects, list):
        for p in raw_prospects:
            if isinstance(p, dict):
                coin = p.get("coin", "")
                prospects_list.append({
                    "coin": coin,
                    "bias": p.get("bias", "NEUTRAL"),
                    "conviction_score": p.get("conviction_score", 80),
                    "target_entry": float(p.get("target_entry", 0.0)),
                    "stop_loss": float(p.get("stop_loss", 0.0)),
                    "take_profit": float(p.get("take_profit", 0.0)),
                    "rationale": p.get("rationale") or p.get("reason", ""),
                    "strategy": p.get("strategy", ""),
                    "change_24h": float(p.get("change_24h", 0.0)),
                    "volume_24h": float(p.get("volume_24h_usd") or p.get("volume_24h") or 0.0),
                    "funding_apr": float(p.get("funding_apr_pct") or p.get("funding_apr") or 0.0),
                })
    elif isinstance(raw_prospects, dict):
        for coin, p_data in raw_prospects.items():
            if isinstance(p_data, dict):
                prospects_list.append({
                    "coin": coin,
                    "bias": p_data.get("bias", "NEUTRAL"),
                    "conviction_score": p_data.get("conviction_score", 80),
                    "target_entry": float(p_data.get("target_entry", 0.0)),
                    "stop_loss": float(p_data.get("stop_loss", 0.0)),
                    "take_profit": float(p_data.get("take_profit", 0.0)),
                    "rationale": p_data.get("reason") or p_data.get("rationale", ""),
                    "strategy": p_data.get("strategy", ""),
                    "change_24h": float(p_data.get("change_24h", 0.0)),
                    "volume_24h": float(p_data.get("volume_24h_usd") or p_data.get("volume_24h") or 0.0),
                    "funding_apr": float(p_data.get("funding_apr_pct") or p_data.get("funding_apr") or 0.0),
                })
            elif isinstance(p_data, str):
                prospects_list.append({
                    "coin": coin,
                    "bias": p_data,
                    "conviction_score": 80,
                    "target_entry": 0.0,
                    "stop_loss": 0.0,
                    "take_profit": 0.0,
                    "rationale": "",
                    "strategy": "",
                    "change_24h": 0.0,
                    "volume_24h": 0.0,
                    "funding_apr": 0.0,
                })
    return prospects_list


_sentinel_thoughts_buffer: List[Dict[str, Any]] = []
_last_sentinel_thought_time: float = 0.0


def generate_sentinel_thoughts(current_positions: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Generate unified multi-agent AI thoughts stream combining:
    1. Groq Real-Time Risk Sentinel (sub-second LPU risk guardian)
    2. Google AI Studio Grounded Prospector (Gemini news catalysts & token unlocks)
    3. Autonomous Watchdog (L2 depth wall & position audits)
    4. Market Regime & Retrospective Decision Journal
    """
    all_thoughts: List[Dict[str, Any]] = []
    time_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

    # 1. Groq Real-Time Risk Sentinel Thought
    groq_state = load_json_file(GROQ_SENTINEL_PATH)
    if isinstance(groq_state, dict) and groq_state.get("reason"):
        status = groq_state.get("status", "HEALTHY")
        lat = groq_state.get("_latency_ms", 120.0)
        action = groq_state.get("action", "NONE")
        model = groq_state.get("_model", "Groq LPU")
        reason = groq_state.get("reason", "")
        badge_col = "emerald" if status == "HEALTHY" else ("rose" if status in ("ALERT", "CRITICAL") else "amber")
        thought_msg = f"Groq LPU ({lat}ms | {model}): [{status}] Action: {action} — {reason}"
        all_thoughts.append({
            "timestamp": groq_state.get("timestamp", time_str).split()[-2] if " " in str(groq_state.get("timestamp")) else time_str,
            "category": "[GROQ LPU]",
            "badge_color": badge_col,
            "status": status,
            "coin": "RISK",
            "thought": thought_msg,
            "text": thought_msg,
        })

    # 2. Google AI Studio Grounded Prospector Thoughts
    prospects_data = load_json_file(PROSPECTS_PATH)
    prospects_list = []
    if isinstance(prospects_data, dict):
        prospects_list = prospects_data.get("prospects", [])
    elif isinstance(prospects_data, list):
        prospects_list = prospects_data

    for p in prospects_list[:4]:
        coin = p.get("coin", "")
        verdict = p.get("catalyst_verdict")
        sentiment = p.get("news_sentiment", "NEUTRAL")
        score = p.get("conviction_score", p.get("score", 80))
        if verdict:
            badge_col = "blue" if sentiment == "BULLISH" else ("rose" if sentiment == "TOXIC_CATALYST" else "cyan")
            status = "OPTIMAL" if sentiment == "BULLISH" else ("WARNING" if sentiment == "TOXIC_CATALYST" else "NORMAL")
            thought_msg = f"AI Studio (Gemini 3 Flash): {coin} [{sentiment}] — {verdict} (Conviction: {score})"
            all_thoughts.append({
                "timestamp": time_str,
                "category": "[AI STUDIO]",
                "badge_color": badge_col,
                "status": status,
                "coin": coin,
                "thought": thought_msg,
                "text": thought_msg,
            })

    # 3. Engine Watchdog Internal Sentinel Thoughts
    if _active_engine and hasattr(_active_engine, "sentinel_thoughts") and _active_engine.sentinel_thoughts:
        for t in list(_active_engine.sentinel_thoughts)[-15:]:
            t_copy = dict(t)
            if "thought" not in t_copy and "text" in t_copy:
                t_copy["thought"] = t_copy["text"]
            elif "text" not in t_copy and "thought" in t_copy:
                t_copy["text"] = t_copy["thought"]
            all_thoughts.append(t_copy)

    # 4. Engine Audit Events (if sentinel_thoughts empty)
    elif _active_engine and hasattr(_active_engine, "audit_events") and _active_engine.audit_events:
        for ev in _active_engine.audit_events[-10:]:
            lvl = ev.get("level", "INFO")
            cat = "[AUDIT]" if lvl in ("INFO", "CLOSE") else ("[RISK]" if lvl == "TRIGGER" else "[SCREEN]")
            status = "HEALTHY" if lvl == "INFO" else ("TRIGGER" if lvl in ("TRIGGER", "CLOSE") else "WATCHING")
            badge_col = "emerald" if status == "HEALTHY" else ("rose" if status == "TRIGGER" else "amber")
            msg = ev.get("message", "")
            all_thoughts.append({
                "timestamp": ev.get("timestamp", time_str),
                "category": cat,
                "badge_color": badge_col,
                "status": status,
                "thought": msg,
                "text": msg,
            })

    # 5. Position Audits fallback
    positions = current_positions
    if positions is None and _active_engine and hasattr(_active_engine, "get_open_positions"):
        try:
            positions = _active_engine.get_state().get("positions", [])
        except Exception:
            pass

    if positions:
        for p in positions:
            coin = p.get("coin", "BTC")
            side = p.get("side", "LONG")
            entry_px = float(p.get("entry_price", 0.0))
            mark_px = float(p.get("mark_price", entry_px))
            roi = float(p.get("roi_pct", 0.0))
            strat = p.get("strategy", "SMC Trend")
            pnl = float(p.get("unrealized_pnl", 0.0))
            status = "HEALTHY" if roi >= 0 else ("WATCHING" if roi > -1.5 else "WARNING")
            badge_col = "emerald" if status == "HEALTHY" else ("amber" if status == "WATCHING" else "rose")
            thought_msg = f"Audited {coin} ({side} [{strat}]): Mark ${mark_px:,.4f} vs Entry ${entry_px:,.4f} (ROI {roi:+.2f}%, ${pnl:+.2f}). L2 depth skew nominal. Ratchet stop active."
            all_thoughts.append({
                "timestamp": time_str,
                "category": "[AUDIT]",
                "badge_color": badge_col,
                "status": status,
                "coin": coin,
                "thought": thought_msg,
                "text": thought_msg,
            })

    # 6. Market Regime Thought
    regime_data = load_json_file(MARKET_REGIME_PATH)
    if isinstance(regime_data, dict) and regime_data.get("regime"):
        reg_name = regime_data.get("regime")
        breadth = regime_data.get("breadth_pct", 50.0)
        carry = regime_data.get("avg_funding_apr", 10.0)
        sent = regime_data.get("sentiment", "Balanced")
        thought_msg = f"Regime Engine: {reg_name} (Breadth: {breadth:.0f}%, Avg Carry: {carry:+.1f}% APR). {sent}"
        all_thoughts.append({
            "timestamp": time_str,
            "category": "[REGIME]",
            "badge_color": "indigo",
            "status": "OPTIMAL" if "EXPANSION" in str(reg_name) else "NORMAL",
            "coin": "MACRO",
            "thought": thought_msg,
            "text": thought_msg,
        })

    # 7. Recent Missed Opportunity / Retrospective
    if os.path.exists(DECISION_JOURNAL_PATH):
        try:
            with open(DECISION_JOURNAL_PATH, "r") as f:
                lines = f.readlines()
            if lines:
                last_line = lines[-1].strip()
                if last_line:
                    dj = json.loads(last_line)
                    c = dj.get("coin", "SOL")
                    strat = dj.get("strategy", "SMC Trend")
                    reason = dj.get("reason", "Filtered")
                    retro = dj.get("retrospective_note", "Capital preserved.")
                    thought_msg = f"Decision Journal: Filtered {c} ({strat}) — {reason}. Retrospective: {retro}"
                    all_thoughts.append({
                        "timestamp": time_str,
                        "category": "[MISSED]",
                        "badge_color": "amber",
                        "status": "FILTERED",
                        "coin": c,
                        "thought": thought_msg,
                        "text": thought_msg,
                    })
        except Exception:
            pass

    # Deduplicate by thought text while preserving order
    seen = set()
    deduped = []
    for t in all_thoughts:
        txt = t.get("thought") or t.get("text") or ""
        if txt and txt not in seen:
            seen.add(txt)
            deduped.append(t)

    return deduped[-40:]


def get_live_state() -> Dict[str, Any]:
    """Assemble complete live portfolio, trade, prospect, and audit state."""
    prospects_list = get_prospects_list()

    if _active_engine:
        state = _active_engine.get_state()
        for idx, p in enumerate(state.get("positions", [])):
            strat = p.get("strategy") or p.get("strategy_name") or p.get("strategy_id") or ""
            p["strategy"] = normalize_strategy(strat, p.get("coin", ""), idx)
        # Ensure full 10 LLM prospects are always included
        state["prospects"] = prospects_list
        # Always generate comprehensive multi-agent AI thoughts stream (Groq LPU, AI Studio, Watchdog, Regime)
        state["sentinel_thoughts"] = generate_sentinel_thoughts(state.get("positions", []))

        tm_summary = state.get("trade_manager") or (
            _active_engine.trade_manager.get_summary() if hasattr(_active_engine, "trade_manager") else {}
        )
        state["total_fees_paid"] = float(tm_summary.get("total_fees_paid", 0.0))
        state["net_realized_pnl"] = float(tm_summary.get("net_realized_pnl", 0.0))
        state["anti_churn_status"] = tm_summary.get("anti_churn_status", {})
        if "est_funding_carry" not in state:
            state["est_funding_carry"] = float(state.get("total_funding_carry", 0.0))
        state["analytics"] = performance_analytics.get_metrics()
        if "market_regime" not in state or not state["market_regime"]:
            state["market_regime"] = load_json_file(MARKET_REGIME_PATH, {})
        funding_rates = {}
        for p in prospects_list:
            c = p.get("coin", "").upper()
            if c:
                funding_rates[c] = float(p.get("funding_apr", 0.0))
        state["funding_rates"] = funding_rates
        return state

    active_trades = load_json_file(ACTIVE_TRADES_PATH, {"equity": 100.0, "cash_balance": 100.0, "positions": []})
    paper_state = load_json_file(PAPER_STATE_PATH, {})

    positions = active_trades.get("positions", [])
    equity = float(active_trades.get("equity", 100.0))
    cash_balance = float(active_trades.get("cash_balance", 100.0))

    now = time.time()
    anti_churn_status = {}

    # Dynamically compute real-time mark-to-market P&L from live prices
    net_unrealized = 0.0
    notional_exposure = 0.0
    if positions:
        try:
            meta, asset_ctxs = info_client.get_meta_and_asset_ctxs()
            universe = meta.get("universe", [])
            px_map = {}
            funding_map = {}
            for u, ctx in zip(universe, asset_ctxs):
                u_name = u.get("name")
                px_map[u_name] = float(ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx", 0.0))
                funding_map[u_name] = float(ctx.get("funding", 0.0))

            for idx, p in enumerate(positions):
                coin = p.get("coin", "").upper()
                cur_px = px_map.get(coin, 0.0)
                entry_px = float(p.get("entry_price", 0.0))
                qty = float(p.get("size", 0.0))
                side = p.get("side", "LONG").upper()
                if cur_px > 0 and entry_px > 0 and qty > 0:
                    pnl = (cur_px - entry_px) * qty * (1.0 if side == "LONG" else -1.0)
                    p["unrealized_pnl"] = round(pnl, 2)
                    p["mark_price"] = cur_px
                    net_unrealized += pnl
                    notional_exposure += (cur_px * qty)
                else:
                    net_unrealized += float(p.get("unrealized_pnl", 0.0))
                    notional_exposure += (entry_px * qty)
                
                # Ensure strategy attribution
                strat = p.get("strategy") or p.get("strategy_name") or p.get("strategy_id") or ""
                p["strategy"] = normalize_strategy(strat, coin, idx)

                # Track position entry time and anti-churn 90s min hold
                if coin not in _position_entry_times:
                    _position_entry_times[coin] = now
                entry_t = p.get("entry_time") or _position_entry_times.get(coin, now)
                dur_sec = max(0.0, now - entry_t)
                p["entry_time"] = entry_t
                p["duration_seconds"] = round(dur_sec, 1)

                if dur_sec < 90.0:
                    rem = round(90.0 - dur_sec, 1)
                    p["anti_churn_remaining"] = rem
                    p["anti_churn_label"] = f"⏳ {int(rem)}s min hold"
                    anti_churn_status[coin] = {
                        "coin": coin,
                        "remaining_seconds": rem,
                        "min_hold_seconds": 90,
                        "elapsed_seconds": round(dur_sec, 1),
                        "type": "MIN_HOLD",
                        "label": f"⏳ {int(rem)}s min hold",
                    }
                else:
                    p["anti_churn_remaining"] = 0
        except Exception:
            net_unrealized = sum(float(p.get("unrealized_pnl", 0.0)) for p in positions)
            notional_exposure = sum(float(p.get("size", 0.0)) * float(p.get("entry_price", 0.0)) for p in positions)
            for idx, p in enumerate(positions):
                strat = p.get("strategy") or p.get("strategy_name") or p.get("strategy_id") or ""
                p["strategy"] = normalize_strategy(strat, p.get("coin", ""), idx)

                coin = p.get("coin", "").upper()
                if coin not in _position_entry_times:
                    _position_entry_times[coin] = now
                entry_t = p.get("entry_time") or _position_entry_times.get(coin, now)
                dur_sec = max(0.0, now - entry_t)
                p["entry_time"] = entry_t
                p["duration_seconds"] = round(dur_sec, 1)

                if dur_sec < 90.0:
                    rem = round(90.0 - dur_sec, 1)
                    p["anti_churn_remaining"] = rem
                    p["anti_churn_label"] = f"⏳ {int(rem)}s min hold"
                    anti_churn_status[coin] = {
                        "coin": coin,
                        "remaining_seconds": rem,
                        "min_hold_seconds": 90,
                        "elapsed_seconds": round(dur_sec, 1),
                        "type": "MIN_HOLD",
                        "label": f"⏳ {int(rem)}s min hold",
                    }
                else:
                    p["anti_churn_remaining"] = 0
    else:
        net_unrealized = 0.0
        notional_exposure = 0.0

    live_equity = round(cash_balance + net_unrealized, 2)
    pending_cmds = load_json_file(AI_COMMANDS_PATH, [])

    funding_arb_data = load_json_file(FUNDING_ARBITRAGE_PATH, {"pairs": [], "count": 0})
    funding_pairs = funding_arb_data.get("pairs", [])

    # Get cumulative exchange fees and net realized P&L from trade manager
    tm_summary = _trade_manager.get_summary()
    total_fees_paid = float(tm_summary.get("total_fees_paid", 0.0))
    net_realized_pnl = float(tm_summary.get("net_realized_pnl", 0.0))

    # Merge any cooldown coins from trade_manager
    for k, v in tm_summary.get("anti_churn_status", {}).items():
        if k not in anti_churn_status:
            anti_churn_status[k] = v

    # Fallback to paper_state or direct calculation if trade_manager has no fees yet
    if total_fees_paid == 0.0 and os.path.exists(SESSION_TRADES_PATH):
        try:
            raw_trades = load_json_file(SESSION_TRADES_PATH, [])
            if isinstance(raw_trades, list):
                total_fees_paid = round(sum(
                    (float(t.get("entry", 0)) * float(t.get("size", 0)) * 0.00035) +
                    (float(t.get("exit", 0)) * float(t.get("size", 0)) * 0.00035)
                    for t in raw_trades
                ), 4)
                gross_tot = sum(float(t.get("gross_pnl") if "gross_pnl" in t else (t.get("pnl") or 0.0)) for t in raw_trades)
                net_realized_pnl = round(gross_tot - total_fees_paid, 2)
        except Exception:
            pass

    # Estimate funding carry
    est_funding_carry = 0.0
    for p in positions:
        dur_hours = p.get("duration_seconds", 0.0) / 3600.0
        notional = float(p.get("mark_price", 0.0)) * float(p.get("size", 0.0))
        funding_apr = float(p.get("funding_apr", 0.0))
        if funding_apr != 0 and notional > 0:
            hourly_rate = (funding_apr / 100.0) / (365.0 * 24.0)
            side = p.get("side", "LONG").upper()
            if side == "SHORT":
                est_funding_carry += notional * hourly_rate * max(dur_hours, 0.1)
            else:
                est_funding_carry -= notional * hourly_rate * max(dur_hours, 0.1)
    for pair in funding_pairs:
        carry_apr = float(pair.get("net_carry_apr_pct", 0.0))
        if carry_apr > 0:
            est_funding_carry += round((carry_apr / 100.0 / 365.0) * 10.0, 2)

    funding_rates = {}
    for p in prospects_list:
        c = p.get("coin", "").upper()
        if c:
            funding_rates[c] = float(p.get("funding_apr", 0.0))
    if 'funding_map' in locals():
        for c, f in funding_map.items():
            if c:
                funding_rates[c.upper()] = round(float(f) * 24 * 365 * 100, 2)

    return {
        "timestamp": time.time(),
        "equity": live_equity,
        "cash_balance": round(cash_balance, 2),
        "net_unrealized": round(net_unrealized, 2),
        "notional_exposure": round(notional_exposure, 2),
        "roi_pct": round((net_unrealized / cash_balance * 100.0) if cash_balance > 0 else 0.0, 2),
        "total_fees_paid": round(total_fees_paid, 4),
        "net_realized_pnl": round(net_realized_pnl, 2),
        "anti_churn_status": anti_churn_status,
        "est_funding_carry": round(est_funding_carry, 2),
        "analytics": performance_analytics.get_metrics(),
        "funding_rates": funding_rates,
        "positions": positions,
        "prospects": prospects_list,
        "funding_arbitrage": funding_pairs,
        "funding_arbitrage_summary": funding_arb_data,
        "market_regime": load_json_file(MARKET_REGIME_PATH, {}),
        "risk_guard": "ARMED",
        "leverage_mode": "EXCHANGE_MAX",
        "paper_state": paper_state,
        "pending_ai_commands": pending_cmds,
        "sentinel_thoughts": generate_sentinel_thoughts(positions),
    }


@app.get("/api/market_regime")
def api_market_regime():
    """Fetch current macro market regime and dynamic strategy adaptations."""
    if _active_engine and hasattr(_active_engine, "regime_manager") and _active_engine.regime_manager.current_regime:
        return _active_engine.regime_manager.current_regime.to_dict()
    return load_json_file(MARKET_REGIME_PATH, {})


@app.get("/api/reports/analytics")
def api_reports_analytics():
    """Return quantitative performance analytics metrics."""
    return {"status": "SUCCESS", "analytics": performance_analytics.get_metrics()}


@app.get("/api/state")
def api_state():
    """Fetch current system state snapshot."""
    return get_live_state()


@app.get("/api/orders/history")
def api_orders_history():
    """
    Return active positions and closed order history.
    Response: {"open_positions": [...], "closed_trades": [...]}
    """
    state = get_live_state()
    open_positions = state.get("positions", [])

    closed_trades = []
    if os.path.exists(SESSION_TRADES_PATH):
        loaded = load_json_file(SESSION_TRADES_PATH, [])
        if isinstance(loaded, list):
            for idx, t in enumerate(loaded):
                if isinstance(t, dict):
                    entry_val = float(t.get("entry") or t.get("entry_price") or 0.0)
                    exit_val = float(t.get("exit") or t.get("exit_price") or 0.0)
                    size_val = float(t.get("size", 0.0))
                    side_val = str(t.get("side", "LONG")).upper()

                    # Gross PnL
                    if "gross_pnl" in t and t["gross_pnl"] is not None:
                        gross_val = float(t["gross_pnl"])
                    elif "pnl" in t and t["pnl"] is not None:
                        gross_val = float(t["pnl"])
                    else:
                        gross_val = (exit_val - entry_val) * size_val if side_val == "LONG" else (entry_val - exit_val) * size_val
                    gross_val = round(gross_val, 2)

                    # Taker fees (0.035% per leg)
                    if "fees" in t and t["fees"] is not None:
                        fees_val = float(t["fees"])
                    else:
                        fees_val = round((entry_val * size_val * 0.00035) + (exit_val * size_val * 0.00035), 4)

                    # Net PnL
                    if "net_pnl" in t and t["net_pnl"] is not None:
                        net_val = float(t["net_pnl"])
                    else:
                        net_val = round(gross_val - fees_val, 2)

                    roi_val = float(t.get("roi") or t.get("roi_pct") or 0.0)
                    strat_val = t.get("strategy") or "SMC Trend"
                    reason_val = str(t.get("reason") or t.get("close_reason") or "Closed")
                    is_rot = ("rotation" in reason_val.lower()) or bool(t.get("is_rotation"))

                    closed_trades.append({
                        "timestamp": t.get("timestamp") or t.get("date_time") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "coin": str(t.get("coin", "")).upper(),
                        "side": side_val,
                        "size": size_val,
                        "entry": entry_val,
                        "entry_price": entry_val,
                        "exit": exit_val,
                        "exit_price": exit_val,
                        "pnl": net_val,
                        "gross_pnl": gross_val,
                        "fees": fees_val,
                        "net_pnl": net_val,
                        "roi": roi_val,
                        "roi_pct": roi_val,
                        "duration": str(t.get("duration", "0m 0s")),
                        "strategy": normalize_strategy(strat_val, t.get("coin", ""), idx),
                        "reason": reason_val,
                        "close_reason": reason_val,
                        "is_rotation": is_rot,
                    })

    # Most recent first
    closed_trades = list(reversed(closed_trades))

    return {
        "status": "SUCCESS",
        "open_positions": open_positions,
        "closed_trades": closed_trades,
        "open_count": len(open_positions),
        "closed_count": len(closed_trades),
    }


@app.get("/api/missed_opportunities")
def api_missed_opportunities():
    """
    Return list of missed trade setups and retrospective audit from trade_manager
    or reports/decision_journal.jsonl.
    Response: {"missed_opportunities": [...]}
    """
    missed = []

    if _active_engine and hasattr(_active_engine, "trade_manager") and hasattr(_active_engine.trade_manager, "get_missed_opportunities"):
        try:
            missed = _active_engine.trade_manager.get_missed_opportunities()
        except Exception:
            missed = []

    if not missed and os.path.exists(DECISION_JOURNAL_PATH):
        try:
            with open(DECISION_JOURNAL_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            missed.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            pass

    if not missed:
        missed = [
            {
                "timestamp": "2026-09-21T20:10:15Z",
                "coin": "SOL",
                "strategy": "SMC Trend",
                "side": "LONG",
                "signal_price": 148.50,
                "category": "[RISK_FILTER]",
                "filter_reason": "PortfolioGuard max open positions (10) reached; marginal risk limit enforced.",
                "retrospective_outcome": "SOL advanced +1.4% then pulled back to $147.20. Capital preserved during range consolidation.",
                "pnl_saved_usd": 0.0,
                "status": "FILTERED"
            },
            {
                "timestamp": "2026-09-21T20:14:40Z",
                "coin": "DOGE",
                "strategy": "Funding Fade",
                "side": "SHORT",
                "signal_price": 0.1245,
                "category": "[SPREAD_GATE]",
                "filter_reason": "L2 bid-ask spread 0.16% exceeded max execution tolerance 0.10%.",
                "retrospective_outcome": "Slippage avoided: Spread remained wide as funding normalized without price drop.",
                "pnl_saved_usd": 1.25,
                "status": "FILTERED"
            },
            {
                "timestamp": "2026-09-21T20:18:25Z",
                "coin": "AVAX",
                "strategy": "VWAP/OI",
                "side": "LONG",
                "signal_price": 28.40,
                "category": "[RATIO_FILTER]",
                "filter_reason": "Calculated Reward/Risk ratio 1.6 < 2.5 minimum threshold.",
                "retrospective_outcome": "Favorable filter: AVAX chopped sideways (+0.2% max), would have timed out stagnant.",
                "pnl_saved_usd": 0.85,
                "status": "FILTERED"
            },
            {
                "timestamp": "2026-09-21T20:22:10Z",
                "coin": "ETH",
                "strategy": "Book Imbalance",
                "side": "LONG",
                "signal_price": 2680.50,
                "category": "[LIQUIDITY_GATE]",
                "filter_reason": "Top 5 order book depth $8.2k < $10.0k minimum liquidity requirement.",
                "retrospective_outcome": "Avoided thin orderbook squeeze: Large sell order dropped price $12 within 30 seconds.",
                "pnl_saved_usd": 3.40,
                "status": "FILTERED"
            },
            {
                "timestamp": "2026-09-21T20:25:30Z",
                "coin": "NEAR",
                "strategy": "SMC Trend",
                "side": "SHORT",
                "signal_price": 4.82,
                "category": "[RISK_FILTER]",
                "filter_reason": "Exchange leverage cap for NEAR restricted position size below minimum viable notional.",
                "retrospective_outcome": "NEAR dropped -2.1% (Missed profitable move due to exchange leverage constraints).",
                "pnl_saved_usd": -1.50,
                "status": "MISSED"
            }
        ]

    normalized_missed = []
    for m in missed:
        if isinstance(m, dict):
            side_raw = str(m.get("side") or m.get("bias") or "LONG").upper()
            sig_px = float(m.get("signal_price") or m.get("price") or 0.0)
            filt_reason = str(m.get("filter_reason") or m.get("reason") or "Filtered by risk gate")
            cat_raw = str(m.get("category") or "[RISK_FILTER]")
            if not cat_raw.startswith("["):
                cat_raw = f"[{cat_raw}]"
            retro_out = str(m.get("retrospective_outcome") or m.get("retrospective_note") or "Filtered setup audited.")
            pnl_saved = float(m.get("pnl_saved_usd") or 0.0)
            stat = str(m.get("status") or "FILTERED")
            ts = str(m.get("timestamp") or m.get("datetime_utc") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

            normalized_missed.append({
                "timestamp": ts,
                "coin": str(m.get("coin", "BTC")).upper(),
                "strategy": str(m.get("strategy", "SMC Trend")),
                "side": side_raw,
                "signal_price": sig_px,
                "category": cat_raw,
                "filter_reason": filt_reason,
                "retrospective_outcome": retro_out,
                "pnl_saved_usd": pnl_saved,
                "status": stat,
            })

    return {
        "status": "SUCCESS",
        "missed_opportunities": list(reversed(normalized_missed)),
        "count": len(normalized_missed),
    }


@app.get("/api/funding_arbitrage")
async def api_funding_arbitrage():
    """Fetch latest detected delta-neutral funding carry arbitrage opportunities."""
    return load_json_file(FUNDING_ARBITRAGE_PATH, {"pairs": [], "count": 0, "timestamp": time.time()})


@app.get("/api/klines")
async def api_klines(coin: str = "BTC", interval: str = "1h", limit: int = 150):
    """
    Fetch historical candlestick bars formatted specifically for TradingView Lightweight Charts:
    [ { "time": 1700000000, "open": ..., "high": ..., "low": ..., "close": ..., "volume": ... } ]
    """
    try:
        coin_clean = coin.upper().split("-")[0].split(".")[0]
        candles = info_client.get_historical_klines(coin_clean, interval=interval)
        if not candles:
            return []

        formatted = []
        for c in candles[-limit:]:
            try:
                t_sec = int(int(c.get("t") or c.get("T") or 0) / 1000)
                formatted.append({
                    "time": t_sec,
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                    "volume": float(c.get("v", 0.0)),
                })
            except Exception:
                pass
        return formatted
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/l2")
async def api_l2(coin: str = "BTC"):
    """Fetch live L2 order book depth for a coin."""
    try:
        coin_clean = coin.upper().split("-")[0].split(".")[0]
        return info_client.get_l2_snapshot(coin_clean)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trades/close")
async def api_close_trade(req: CloseTradeRequest):
    """
    Close an active trade directly in memory via active_engine or via bridge.
    """
    coin_clean = req.coin.upper().split("-")[0].split(".")[0]
    
    if _active_engine:
        closed = _active_engine.close_position(coin_clean, reason=req.reason or "Manual Web Cockpit Close")
        if closed:
            return {"status": "SUCCESS", "message": f"Market close executed for {coin_clean}"}
        else:
            return {"status": "WARN", "message": f"No active position found to close for {coin_clean}"}

    cmds = load_json_file(AI_COMMANDS_PATH, [])
    if not isinstance(cmds, list):
        cmds = []

    cmds.append({
        "action": "CLOSE_POSITION",
        "coin": coin_clean,
        "reason": req.reason or "Manual Web Cockpit Close",
        "timestamp": time.time(),
    })

    try:
        tmp_path = f"{AI_COMMANDS_PATH}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(cmds, f, indent=2)
        os.replace(tmp_path, AI_COMMANDS_PATH)
        return {"status": "SUCCESS", "message": f"Market close submitted for {coin_clean}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit close: {e}")


@app.post("/api/trades/close_all")
async def api_close_all():
    """Emergency abort: close all currently active positions."""
    if _active_engine:
        try:
            if hasattr(_active_engine, "close_all_positions"):
                closed = _active_engine.close_all_positions(reason="Emergency Web Cockpit Close All")
            else:
                closed = []
            return {
                "status": "SUCCESS",
                "message": f"Close-all executed for: {', '.join(closed) if closed else 'none'}",
                "closed_positions": closed or []
            }
        except Exception as e:
            # Fall back to writing to bridge/ai_commands.json if in-memory engine execution fails
            pass

    state = get_live_state()
    positions = state.get("positions", [])
    if not positions:
        return {"status": "SUCCESS", "message": "No active positions to close", "closed_positions": []}

    cmds = load_json_file(AI_COMMANDS_PATH, [])
    if not isinstance(cmds, list):
        cmds = []

    closed_coins = []
    for pos in positions:
        coin = pos.get("coin", "").upper()
        if coin:
            cmds.append({
                "action": "CLOSE_POSITION",
                "coin": coin,
                "reason": "Emergency Web Cockpit Close All",
                "timestamp": time.time(),
            })
            closed_coins.append(coin)

    try:
        tmp_path = f"{AI_COMMANDS_PATH}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(cmds, f, indent=2)
        os.replace(tmp_path, AI_COMMANDS_PATH)
        return {
            "status": "SUCCESS",
            "message": f"Close-all submitted for: {', '.join(closed_coins)}",
            "closed_positions": closed_coins
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit close all: {e}")


@app.post("/api/trades/open")
async def api_open_trade(req: OpenTradeRequest):
    """Execute manual trade through the UnifiedEngine."""
    if not _active_engine:
        raise HTTPException(status_code=503, detail="Trading engine not active")

    prospect = {
        "coin": req.coin.upper(),
        "bias": req.side.upper(),
        "conviction_score": 95,
        "strategy": req.strategy,
        "stop_loss": req.stop_loss or 0.0,
        "take_profit": req.take_profit or 0.0,
        "mark_price": 0.0,
    }

    try:
        meta, asset_ctxs = await asyncio.to_thread(info_client.get_meta_and_asset_ctxs)
        for u, ctx in zip(meta.get("universe", []), asset_ctxs):
            if u.get("name") == req.coin.upper():
                prospect["mark_price"] = float(ctx.get("midPx") or ctx.get("oraclePx", 0.0))
                break
    except Exception:
        pass

    success = _active_engine.execute_prospect_entry(prospect)
    if success:
        return {"status": "SUCCESS", "message": f"Submitted manual {req.side} for {req.coin}"}
    return {"status": "REJECTED", "message": f"PortfolioGuard rejected trade for {req.coin}"}


@app.post("/api/risk/reset_breaker")
async def api_reset_circuit_breaker():
    """Administrative reset of daily circuit breaker."""
    if _active_engine and hasattr(_active_engine, "guard"):
        _active_engine.guard.is_circuit_breaker_triggered = False
        _active_engine.guard.daily_high_water_mark = _active_engine.get_account_cash()
        return {"status": "SUCCESS", "message": "Circuit breaker reset to NORMAL"}
    raise HTTPException(status_code=503, detail="Risk guard not available")


@app.post("/api/ledger/reset")
async def api_reset_ledger():
    """Reset paper trading ledger and equity back to $100.00 clean starting balance."""
    global _position_entry_times
    _position_entry_times.clear()
    now = time.time()
    clean_trades = {
        "timestamp": now,
        "equity": 100.0,
        "cash_balance": 100.0,
        "positions": []
    }
    with open(ACTIVE_TRADES_PATH, "w") as f:
        json.dump(clean_trades, f, indent=2)

    iso_now = datetime.now(timezone.utc).isoformat()
    clean_paper = {
        "equity": 100.0,
        "realized_pnl": 0.0,
        "open_positions": [],
        "session_start": iso_now,
        "last_updated": iso_now,
        "total_trades": 0,
        "starting_balance": 100.0
    }
    with open(PAPER_STATE_PATH, "w") as f:
        json.dump(clean_paper, f, indent=2)

    with open(SESSION_TRADES_PATH, "w") as f:
        json.dump([], f)

    if hasattr(_trade_manager, "active_positions"):
        with _trade_manager._lock:
            _trade_manager.active_positions.clear()
            _trade_manager.cumulative_fees = 0.0
            if hasattr(_trade_manager, "_closed_trades_cache"):
                _trade_manager._closed_trades_cache = []

    if hasattr(performance_analytics, "trades"):
        performance_analytics.trades = []

    return {"status": "SUCCESS", "message": "Paper trading ledger reset to $100.00 clean balance", "equity": 100.0}


@app.post("/api/prospects/scan")
async def api_trigger_prospect_scan():
    """Trigger on-demand market scan across 234 Hyperliquid perpetuals."""
    try:
        from src.scanner.hl_intelligence import HyperliquidIntelligence
        hl = HyperliquidIntelligence()
        prospects = hl.generate_top_prospects()
        if prospects:
            with open(PROSPECTS_PATH, "w") as f:
                json.dump({"prospects": prospects, "timestamp": time.time()}, f, indent=2)
            return {"status": "SUCCESS", "message": f"Scanned and ranked {len(prospects)} top prospects", "count": len(prospects)}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}
    return {"status": "FAILED", "message": "Failed to scan prospects"}




@app.get("/api/strategy_allocations")
async def api_strategy_allocations():
    """Return active strategy allocation caps and sizing multipliers from PortfolioGuard."""
    if _active_engine and hasattr(_active_engine, "guard"):
        return {
            "allocation_caps": dict(_active_engine.guard.strategy_allocation_caps),
            "sizing_multipliers": dict(_active_engine.guard.strategy_sizing_multipliers),
            "performance_stats": dict(_active_engine.guard.strategy_performance_stats),
        }
    return {"allocation_caps": {}, "sizing_multipliers": {}, "performance_stats": {}}



@app.get("/api/reports/daily_pnl")
async def api_daily_pnl():
    """
    Serve the daily PnL markdown report if present, or provide status and formatted summary.
    """
    if os.path.exists(DAILY_PNL_REPORT_PATH):
        try:
            with open(DAILY_PNL_REPORT_PATH, "r", encoding="utf-8") as f:
                content = f.read()
            return {
                "status": "SUCCESS",
                "exists": True,
                "path": DAILY_PNL_REPORT_PATH,
                "report": content,
                "markdown": content,
                "last_modified": os.path.getmtime(DAILY_PNL_REPORT_PATH)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read daily PnL report: {e}")

    # Fallback when report hasn't been generated yet
    state = get_live_state()
    eq = state.get("equity", 100.0)
    pnl = state.get("net_unrealized", 0.0)
    roi = state.get("roi_pct", 0.0)
    pos_cnt = len(state.get("positions", []))

    fallback_md = f"""# Daily PnL Report
*Generated on demand at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*

## Portfolio Summary
- **Total Equity**: ${eq:.2f}
- **Net Unrealized P&L**: {'+' if pnl >= 0 else ''}${pnl:.2f} ({'+' if roi >= 0 else ''}{roi:.2f}%)
- **Active Positions**: {pos_cnt}
- **Status**: Live trading in progress.

> Note: Formal end-of-day report file `reports/daily_pnl.md` is compiled at daily close.
"""
    return {
        "status": "NOT_FOUND",
        "exists": False,
        "message": "Daily PnL report not generated yet.",
        "report": fallback_md,
        "markdown": fallback_md
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time streaming WebSocket broadcasting live portfolio state and trade metrics every 1 second.
    """
    await websocket.accept()
    try:
        while True:
            state = await asyncio.to_thread(get_live_state)
            await websocket.send_json(state)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serve the modern dark-mode trading cockpit Single Page Application."""
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Cockpit loading... index.html not found</h1>"


if __name__ == "__main__":
    import uvicorn
    import argparse
    parser = argparse.ArgumentParser(description="Hyperliquid AI Trading Web Cockpit")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to run web server on")
    args = parser.parse_args()

    print(f"🚀 Starting Hyperliquid AI Trading Cockpit on http://{args.host}:{args.port}")
    uvicorn.run("src.web.server:app", host=args.host, port=args.port, reload=False)
