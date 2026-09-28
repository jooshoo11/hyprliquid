import json
from collections import Counter
from datetime import datetime

with open("reports/session_trades.json") as f:
    trades = json.load(f)

recent = [t for t in trades if t.get("timestamp", "") >= "2026-09-26T17:00:00"]

strats = {}
for t in recent:
    s = t.get("strategy", "Unknown").split("-")[0]
    if s not in strats:
        strats[s] = {"trades": [], "gross": 0.0, "fees": 0.0, "net": 0.0, "wins": 0, "losses": 0}
    strats[s]["trades"].append(t)
    gross = t.get("gross_pnl", t.get("pnl", 0.0))
    fees = t.get("fees", 0.0)
    net = t.get("net_pnl", t.get("pnl", 0.0))
    strats[s]["gross"] += gross
    strats[s]["fees"] += fees
    strats[s]["net"] += net
    if net > 0:
        strats[s]["wins"] += 1
    else:
        strats[s]["losses"] += 1

print("=" * 80)
print("AUDIT: PERFORMANCE BREAKDOWN (PAST 28 HOURS: Sept 26 17:00 - Sept 27 20:40 UTC)")
print("=" * 80)
for name, s in sorted(strats.items(), key=lambda x: x[1]["net"]):
    n = len(s["trades"])
    wr = (s["wins"] / n * 100) if n else 0
    w_trades = [t.get("net_pnl", 0) for t in s["trades"] if t.get("net_pnl", 0) > 0]
    l_trades = [t.get("net_pnl", 0) for t in s["trades"] if t.get("net_pnl", 0) <= 0]
    avg_win = (sum(w_trades) / len(w_trades)) if w_trades else 0.0
    avg_loss = (sum(l_trades) / len(l_trades)) if l_trades else 0.0
    wins = s["wins"]
    losses = s["losses"]
    gross = s["gross"]
    fees = s["fees"]
    net = s["net"]
    print(f"{name:24s} | {n:2d} trades ({n/len(recent)*100:4.1f}%) | WR: {wr:4.1f}% ({wins}W/{losses}L) | Gross: ${gross:6.2f} | Fees: ${fees:4.2f} | Net: ${net:6.2f} | AvgW: ${avg_win:4.2f} | AvgL: ${avg_loss:4.2f}")

total_net = sum(s["net"] for s in strats.values())
total_gross = sum(s["gross"] for s in strats.values())
total_fees = sum(s["fees"] for s in strats.values())
total_wins = sum(s["wins"] for s in strats.values())
total_trades = len(recent)
print("-" * 80)
print(f"TOTAL: {total_trades} trades | WinRate: {total_wins/total_trades*100:.1f}% | Gross: ${total_gross:.2f} | Fees: ${total_fees:.2f} | Net: ${total_net:.2f}")

print("\n" + "=" * 80)
print("ORDERBOOK IMBALANCE: EXIT REASONS (51 trades)")
print("=" * 80)
ob_reasons = Counter()
for t in strats["OrderBookImbalance"]["trades"]:
    r = t.get("reason", "")
    if "Orderbook depth wall collapsed" in r:
        ob_reasons["Orderbook depth wall collapsed (>6x adverse skew)"] += 1
    elif "Trailing stop" in r:
        ob_reasons["Trailing stop triggered (+0.75% trailing)"] += 1
    elif "Breakeven ratchet" in r:
        ob_reasons["Breakeven ratchet triggered (+0.1% ROI)"] += 1
    elif "MAE hard cut" in r:
        ob_reasons["MAE hard cut (-2.5% ROI / -$10)"] += 1
    elif "Stagnant trade" in r:
        ob_reasons["Stagnant trade exit (>4h flat)"] += 1
    elif "funding" in r.lower():
        ob_reasons["Adverse funding spike"] += 1
    else:
        ob_reasons[r[:40]] += 1

for r, count in ob_reasons.most_common():
    pct = count / len(strats["OrderBookImbalance"]["trades"]) * 100
    print(f"  {count:2d}x ({pct:4.1f}%): {r}")

print("\n" + "=" * 80)
print("TIME IN TRADE DISTRIBUTION (OrderBookImbalance)")
print("=" * 80)
durations = []
for t in strats["OrderBookImbalance"]["trades"]:
    dur_str = t.get("duration", "0h 0m 0s")
    # parse 'Xh Ym Zs'
    parts = dur_str.split()
    secs = 0
    for p in parts:
        if p.endswith("h"): secs += int(p[:-1]) * 3600
        elif p.endswith("m"): secs += int(p[:-1]) * 60
        elif p.endswith("s"): secs += int(p[:-1])
    durations.append(secs)

print(f"  Shortest hold: {min(durations)}s ({min(durations)/60:.1f}m)")
print(f"  Longest hold:  {max(durations)}s ({max(durations)/3600:.1f}h)")
print(f"  Average hold:  {sum(durations)/len(durations):.0f}s ({(sum(durations)/len(durations))/60:.1f}m)")
print(f"  Trades held < 15 mins: {sum(1 for d in durations if d < 900)} ({sum(1 for d in durations if d < 900)/len(durations)*100:.1f}%)")
print(f"  Trades held 15m - 1h:  {sum(1 for d in durations if 900 <= d < 3600)} ({sum(1 for d in durations if 900 <= d < 3600)/len(durations)*100:.1f}%)")
print(f"  Trades held > 1h:      {sum(1 for d in durations if d >= 3600)} ({sum(1 for d in durations if d >= 3600)/len(durations)*100:.1f}%)")
