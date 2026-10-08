"""
Dynamic Strategy Allocator & Performance-Weighted Capital Sizing (src/risk/dynamic_allocator.py)

Autonomously allocates more capital to winning, outperforming strategies while throttling underperforming strategies.

Performance Tiers & Capital Allocation Multipliers:
1. 🔥 SUPER-PERFORMER (HOT):
   - Win Rate >= 70% and Net PnL > 0 (or Profit Factor >= 2.0, or 3+ win streak)
   - Capital Multiplier: 1.6x - 2.0x (+60% to +100% larger position margin)
2. ⚡ SOLID-PERFORMER (WARM):
   - Win Rate >= 55% and Net PnL > 0 (or Profit Factor >= 1.4)
   - Capital Multiplier: 1.3x (+30% larger position margin)
3. ⚖️ BASELINE (NORMAL):
   - Balanced performance (40% - 55% win rate)
   - Capital Multiplier: 1.0x (Standard baseline allocation)
4. ❄️ UNDERPERFORMER (COLD):
   - Win Rate < 40% or Net PnL < 0
   - Capital Multiplier: 0.6x (-40% capital reduction to preserve drawdown)
5. 🛑 CIRCUIT BREAKER (PROBATION):
   - 3 consecutive losses or heavy drawdown
   - Capital Multiplier: 0.3x or temporary cooldown pause
"""

import os
import json
import time
from typing import Dict, Any, List, Optional
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[2])


class DynamicStrategyAllocator:
    def __init__(self, session_trades_file: Optional[str] = None):
        self.session_trades_file = session_trades_file or os.path.join(REPO_ROOT, "reports", "session_trades.json")
        self.cached_multipliers: Dict[str, float] = {}
        self.cached_stats: Dict[str, Dict[str, Any]] = {}

    def normalize_strategy_name(self, name: str) -> str:
        """Standardize strategy identifiers to friendly names."""
        if not name:
            return "TrendContinuationSMC"
        s = str(name).strip()
        if "smc" in s.lower() or "continuation" in s.lower() or "trend" in s.lower():
            return "TrendContinuationSMC"
        if "funding" in s.lower() or "fade" in s.lower():
            return "HourlyFundingFade"
        if "squeeze" in s.lower():
            return "ShortSqueezeIgnition"
        if "vwap" in s.lower() or "momentum" in s.lower():
            return "VwapOiMomentum"
        if "imbalance" in s.lower() or "book" in s.lower() or "scalp" in s.lower():
            return "OrderBookImbalance"
        return s

    def evaluate_allocations(self, trades: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Evaluate closed trades and compute dynamic capital allocation multipliers per strategy.
        """
        if trades is None:
            if os.path.exists(self.session_trades_file):
                try:
                    with open(self.session_trades_file, "r") as f:
                        trades = json.load(f)
                except Exception:
                    trades = []
            else:
                trades = []

        # Standard strategy universe
        strategies = [
            "TrendContinuationSMC",
            "HourlyFundingFade",
            "ShortSqueezeIgnition",
            "VwapOiMomentum",
            "OrderBookImbalance",
        ]

        strat_trades: Dict[str, List[Dict[str, Any]]] = {s: [] for s in strategies}
        for t in trades or []:
            raw_s = t.get("strategy", "")
            s_norm = self.normalize_strategy_name(raw_s)
            strat_trades.setdefault(s_norm, []).append(t)

        allocations: Dict[str, Dict[str, Any]] = {}
        multipliers: Dict[str, float] = {}

        for strat, t_list in strat_trades.items():
            if not t_list:
                # Default baseline when no trades yet
                allocations[strat] = {
                    "strategy": strat,
                    "tier": "NORMAL ⚖️",
                    "multiplier": 1.0,
                    "trades_count": 0,
                    "win_rate_pct": 50.0,
                    "profit_factor": 1.0,
                    "net_pnl": 0.0,
                    "bonus_pct": "+0%",
                    "status_label": "Baseline (1.0x)",
                }
                multipliers[strat] = 1.0
                continue

            wins = 0
            gross_wins = 0.0
            gross_losses = 0.0
            net_pnl = 0.0
            consecutive_wins = 0
            consecutive_losses = 0

            # Last 20 rolling trades
            recent_trades = t_list[-20:]
            for tr in recent_trades:
                gross = float(tr.get("gross_pnl") if "gross_pnl" in tr else (tr.get("pnl") or 0.0))
                fees = float(tr.get("fees") or 0.0)
                net = float(tr.get("net_pnl") if "net_pnl" in tr else (gross - fees))
                net_pnl += net

                if net > 0:
                    wins += 1
                    gross_wins += gross
                elif net < 0:
                    gross_losses += abs(gross)

            # Streak tracking from latest trades
            for tr in reversed(recent_trades):
                net = float(tr.get("net_pnl") if "net_pnl" in tr else (tr.get("pnl") or 0.0))
                if net > 0:
                    if consecutive_losses == 0:
                        consecutive_wins += 1
                    else:
                        break
                elif net < 0:
                    if consecutive_wins == 0:
                        consecutive_losses += 1
                    else:
                        break

            total_cnt = len(recent_trades)
            win_rate = (wins / total_cnt) * 100.0 if total_cnt > 0 else 50.0
            profit_factor = round(gross_wins / max(gross_losses, 0.01), 2) if gross_losses > 0 else (2.5 if gross_wins > 0 else 1.0)

            # Dynamic Tier Assignment
            if (win_rate >= 70.0 and net_pnl > 0) or profit_factor >= 2.0 or consecutive_wins >= 3:
                multiplier = 1.8 if (win_rate >= 80.0 or consecutive_wins >= 4) else 1.6
                tier = "HOT 🔥 (OVERPERFORMING)"
                bonus_pct = f"+{int((multiplier - 1.0) * 100)}%"
                status_label = f"Expanded Capital ({multiplier:.1f}x)"
            elif (win_rate >= 55.0 and net_pnl > 0) or profit_factor >= 1.4:
                multiplier = 1.3
                tier = "WARM ⚡ (SOLID WINNER)"
                bonus_pct = "+30%"
                status_label = "Boosted (+30%)"
            elif win_rate < 35.0 or consecutive_losses >= 3 or net_pnl < -10.0:
                multiplier = 0.4 if (consecutive_losses >= 3 or win_rate < 25.0) else 0.6
                tier = "PROBATION 🛑 (THROTTLED)"
                bonus_pct = f"-{int((1.0 - multiplier) * 100)}%"
                status_label = f"Defensive Cut ({multiplier:.1f}x)"
            elif net_pnl < 0 or win_rate < 45.0:
                multiplier = 0.8
                tier = "COLD ❄️ (REDUCED)"
                bonus_pct = "-20%"
                status_label = "Reduced (-20%)"
            else:
                multiplier = 1.0
                tier = "NORMAL ⚖️"
                bonus_pct = "+0%"
                status_label = "Baseline (1.0x)"

            allocations[strat] = {
                "strategy": strat,
                "tier": tier,
                "multiplier": multiplier,
                "trades_count": total_cnt,
                "win_rate_pct": round(win_rate, 1),
                "profit_factor": profit_factor,
                "net_pnl": round(net_pnl, 2),
                "consecutive_wins": consecutive_wins,
                "consecutive_losses": consecutive_losses,
                "bonus_pct": bonus_pct,
                "status_label": status_label,
            }
            multipliers[strat] = multiplier

        self.cached_multipliers = multipliers
        self.cached_stats = allocations

        return {
            "status": "SUCCESS",
            "multipliers": multipliers,
            "allocations": allocations,
            "timestamp": time.time(),
        }

    def get_multiplier(self, strategy_name: str) -> float:
        """Get dynamic capital multiplier for a strategy (defaults to 1.0 if not evaluated)."""
        norm = self.normalize_strategy_name(strategy_name)
        if norm in self.cached_multipliers:
            return self.cached_multipliers[norm]
        self.evaluate_allocations()
        return self.cached_multipliers.get(norm, 1.0)
