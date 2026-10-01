import json
from datetime import datetime

with open("reports/session_trades.json") as f:
    trades = json.load(f)

cum_pnl = 0.0
equity = 100.0
peak_equity = 100.0
peak_idx = 0
peak_time = ""

history = []
for i, t in enumerate(trades):
    net = t.get("net_pnl", t.get("pnl", 0.0))
    cum_pnl += net
    equity += net
    if equity > peak_equity:
        peak_equity = equity
        peak_idx = i + 1
        peak_time = t.get("timestamp")
    strat = t.get("strategy", "").split("-")[0]
    history.append({
        "num": i + 1,
        "time": t.get("timestamp"),
        "coin": t.get("coin"),
        "side": t.get("side"),
        "strategy": strat,
        "size": t.get("size"),
        "entry": t.get("entry"),
        "exit": t.get("exit"),
        "net": net,
        "cum_pnl": cum_pnl,
        "equity": equity,
        "reason": t.get("reason"),
    })

print(f"Total Trades in Journal: {len(trades)}")
print(f"Peak Equity was ${peak_equity:.2f} at Trade #{peak_idx} ({peak_time})")
print(f"Ending Equity: ${equity:.2f} (Net: ${cum_pnl:+.2f})")
print("\n" + "="*120)
print(f"{'#':<3} {'Time (UTC)':<20} {'Coin':<8} {'Side':<5} {'Strategy':<20} {'Net PnL':<10} {'Equity':<10} {'Reason'}")
print("="*120)

for h in history:
    print(f"{h['num']:<3} {h['time']:<20} {h['coin']:<8} {h['side']:<5} {h['strategy']:<20} {h['net']:<+10.2f} {h['equity']:<10.2f} {h['reason'][:50]}")
