"""
Quant Performance Analytics Engine (src/risk/performance_analytics.py).

Computes real-time execution performance metrics from session and historical trades:
- Win Rate %
- Profit Factor
- Sharpe Ratio
- Expectancy ($/trade)
- Total Net P&L & Gross P&L
- Max Drawdown %
"""

import os
import json
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[2])
SESSION_TRADES_PATH = os.path.join(REPO_ROOT, "reports", "session_trades.json")


class PerformanceAnalytics:
    """
    Quant performance analytics engine computing real-time trade statistics:
    Win Rate, Profit Factor, Sharpe Ratio, Expectancy, and Total Net P&L.
    """

    def __init__(self, session_trades_path: Optional[str] = None) -> None:
        self.session_trades_path = session_trades_path or SESSION_TRADES_PATH

    def get_metrics(self, trades: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Compute quantitative performance metrics from closed trades.
        """
        if trades is None:
            trades = []
            if os.path.exists(self.session_trades_path):
                try:
                    with open(self.session_trades_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, list):
                            trades = loaded
                except Exception:
                    trades = []

        total_trades = len(trades)
        if total_trades == 0:
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

        gross_profits = []
        gross_losses = []
        net_pnls = []
        total_fees = 0.0

        for t in trades:
            entry = float(t.get("entry") or t.get("entry_price") or 0.0)
            exit_px = float(t.get("exit") or t.get("exit_price") or 0.0)
            size = float(t.get("size", 0.0))

            gross = float(t.get("gross_pnl") if "gross_pnl" in t else (t.get("pnl") or 0.0))
            fees = float(t.get("fees") if "fees" in t else round((entry * size * 0.00035) + (exit_px * size * 0.00035), 4))
            net = float(t.get("net_pnl") if "net_pnl" in t else (gross - fees))

            total_fees += fees
            net_pnls.append(net)
            if net > 0:
                gross_profits.append(net)
            elif net < 0:
                gross_losses.append(abs(net))

        win_count = len(gross_profits)
        loss_count = len(gross_losses)
        win_rate = (win_count / total_trades) * 100.0 if total_trades > 0 else 0.0

        sum_profit = sum(gross_profits)
        sum_loss = sum(gross_losses)
        profit_factor = (sum_profit / sum_loss) if sum_loss > 0 else (99.0 if sum_profit > 0 else 0.0)

        total_net_pnl = sum(net_pnls)
        total_gross_pnl = sum(float(t.get("gross_pnl") if "gross_pnl" in t else (t.get("pnl") or 0.0)) for t in trades)
        expectancy = total_net_pnl / total_trades if total_trades > 0 else 0.0

        # Sharpe Ratio calculation from returns
        if len(net_pnls) > 1:
            mean_pnl = total_net_pnl / total_trades
            variance = sum((p - mean_pnl) ** 2 for p in net_pnls) / (total_trades - 1)
            std_pnl = math.sqrt(variance) if variance > 0 else 0.0
            sharpe_ratio = (mean_pnl / std_pnl) * math.sqrt(252) if std_pnl > 0 else 0.0
        else:
            sharpe_ratio = 0.0

        avg_win = (sum_profit / win_count) if win_count > 0 else 0.0
        avg_loss = (sum_loss / loss_count) if loss_count > 0 else 0.0

        # Max Drawdown calculation from cumulative equity curve
        cum_equity = 100.0
        peak_equity = 100.0
        max_dd_pct = 0.0
        for p in net_pnls:
            cum_equity += p
            peak_equity = max(peak_equity, cum_equity)
            if peak_equity > 0:
                dd = ((peak_equity - cum_equity) / peak_equity) * 100.0
                max_dd_pct = max(max_dd_pct, dd)

        return {
            "total_trades": total_trades,
            "winning_trades": win_count,
            "losing_trades": loss_count,
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "expectancy_usd": round(expectancy, 2),
            "total_net_pnl": round(total_net_pnl, 2),
            "total_gross_pnl": round(total_gross_pnl, 2),
            "total_fees_paid": round(total_fees, 4),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "avg_win_usd": round(avg_win, 2),
            "avg_loss_usd": round(avg_loss, 2),
        }

    def generate_markdown_report(
        self,
        trades: Optional[List[Dict[str, Any]]] = None,
        output_file: Optional[str] = None,
    ) -> str:
        """
        Generate a comprehensive quantitative performance report in Markdown format.
        Saves the report to output_file (default: reports/performance_analytics.md) and returns it.
        """
        if trades is None:
            trades = []
            if os.path.exists(self.session_trades_path):
                try:
                    with open(self.session_trades_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, list):
                            trades = loaded
                except Exception:
                    trades = []

        metrics = self.get_metrics(trades)
        target_path = output_file or os.path.join(REPO_ROOT, "reports", "performance_analytics.md")
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Strategy breakdown
        strat_trades: Dict[str, List[Dict[str, Any]]] = {}
        for t in trades:
            s = t.get("strategy") or "Unknown"
            strat_trades.setdefault(s, []).append(t)

        strat_rows = []
        for s, s_trades in strat_trades.items():
            s_metrics = self.get_metrics(s_trades)
            strat_rows.append(
                f"| {s} | {s_metrics['total_trades']} | {s_metrics['win_rate_pct']:.1f}% | "
                f"${s_metrics['total_net_pnl']:+.2f} | {s_metrics['profit_factor']:.2f} | "
                f"${s_metrics['expectancy_usd']:+.2f} |"
            )
        strat_table = "\n".join(strat_rows) if strat_rows else "| None | 0 | 0.0% | $0.00 | 0.00 | $0.00 |"

        # Recent trades table (up to 10)
        recent = trades[-10:] if trades else []
        trade_rows = []
        for t in reversed(recent):
            dt = t.get("timestamp") or t.get("exit_time") or "N/A"
            coin = t.get("coin", "N/A")
            side = t.get("side", "N/A")
            size = t.get("size", 0.0)
            entry = float(t.get("entry") or t.get("entry_price") or 0.0)
            exit_px = float(t.get("exit") or t.get("exit_price") or 0.0)
            gross = float(t.get("gross_pnl") if "gross_pnl" in t else (t.get("pnl") or 0.0))
            fees = float(t.get("fees") or 0.0)
            net = float(t.get("net_pnl") if "net_pnl" in t else (gross - fees))
            roi = float(t.get("roi") or 0.0)
            reason = t.get("reason", "N/A")
            trade_rows.append(
                f"| {dt} | {coin} | {side} | {size} | ${entry:,.4f} | ${exit_px:,.4f} | "
                f"${gross:+.2f} | ${fees:.4f} | ${net:+.2f} | {roi:+.2f}% | {reason} |"
            )
        trade_table = "\n".join(trade_rows) if trade_rows else "| N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |"

        md = f"""# Quantitative Performance Analytics Report

**Generated:** {now_utc}  
**Source Data:** `{os.path.basename(self.session_trades_path)}` ({metrics['total_trades']} closed trades)

## Key Performance Indicators (KPIs)

| Metric | Value |
|---|---|
| **Total Closed Trades** | {metrics['total_trades']} |
| **Win Rate** | {metrics['win_rate_pct']:.1f}% ({metrics['winning_trades']}W / {metrics['losing_trades']}L) |
| **Profit Factor** | {metrics['profit_factor']:.2f} |
| **Annualized Sharpe Ratio** | {metrics['sharpe_ratio']:.2f} |
| **Expectancy per Trade** | ${metrics['expectancy_usd']:+.2f} |
| **Total Gross P&L** | ${metrics['total_gross_pnl']:+.2f} |
| **Total Taker Fees Paid** | ${metrics['total_fees_paid']:.4f} |
| **Total Net P&L** | ${metrics['total_net_pnl']:+.2f} |
| **Average Win** | ${metrics['avg_win_usd']:.2f} |
| **Average Loss** | ${metrics['avg_loss_usd']:.2f} |
| **Max Drawdown** | {metrics['max_drawdown_pct']:.2f}% |

## Strategy Performance Breakdown

| Strategy | Trades | Win Rate | Net PnL | Profit Factor | Expectancy |
|---|---|---|---|---|---|
{strat_table}

## Recent Executions (Last 10 Trades)

| Timestamp (UTC) | Coin | Side | Size | Entry Px | Exit Px | Gross PnL | Fees | Net PnL | ROI | Reason |
|---|---|---|---|---|---|---|---|---|---|---|
{trade_table}
"""
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(md)

        return md

