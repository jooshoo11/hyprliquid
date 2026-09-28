import json
from collections import defaultdict

with open("reports/session_trades.json") as f:
    trades = json.load(f)

strat_pnl = defaultdict(lambda: {"wins": 0, "losses": 0, "net": 0.0, "fees": 0.0, "gross": 0.0})

for t in trades[-100:]:
    s = t.get("strategy", "Unknown").split("-")[0]
    net_pnl = float(t.get("net_pnl", t.get("pnl", 0.0)))
    fees = float(t.get("fees", 0.0))
    gross = float(t.get("gross_pnl", net_pnl + fees))
    strat_pnl[s]["net"] += net_pnl
    strat_pnl[s]["fees"] += fees
    strat_pnl[s]["gross"] += gross
    if net_pnl > 0:
        strat_pnl[s]["wins"] += 1
    elif net_pnl < 0:
        strat_pnl[s]["losses"] += 1

print(f"{'Strategy':25} | {'Wins':5} | {'Losses':6} | {'WinRate':8} | {'Gross PnL':10} | {'Fees':8} | {'Net PnL':10}")
print("-" * 85)
for s, stats in sorted(strat_pnl.items(), key=lambda x: x[1]["net"]):
    total = stats["wins"] + stats["losses"]
    wr = (stats["wins"] / total * 100) if total > 0 else 0.0
    print(f"{s:25} | {stats['wins']:5} | {stats['losses']:6} | {wr:6.1f}%  | ${stats['gross']:9.2f} | ${stats['fees']:6.2f} | ${stats['net']:9.2f}")
