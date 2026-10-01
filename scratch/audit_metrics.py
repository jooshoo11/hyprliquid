import json

with open("reports/session_trades.json") as f:
    trades = json.load(f)

print(f"Total trades: {len(trades)}")
wins = [t for t in trades if t["net_pnl"] > 0]
losses = [t for t in trades if t["net_pnl"] < 0]
gross_profit = sum(t["net_pnl"] for t in wins)
gross_loss = abs(sum(t["net_pnl"] for t in losses))
pf = gross_profit / gross_loss if gross_loss > 0 else 999.0
print(f"Wins: {len(wins)}, Losses: {len(losses)}, Win rate: {len(wins)/len(trades)*100:.1f}%")
print(f"Gross Profit: ${gross_profit:.2f}, Gross Loss: ${gross_loss:.2f}, Profit Factor: {pf:.2f}")
if wins:
    print(f"Avg Win: ${gross_profit/len(wins):.2f}")
if losses:
    print(f"Avg Loss: ${gross_loss/len(losses):.2f}")
if wins and losses:
    print(f"Payoff Ratio (Avg Win / Avg Loss): {(gross_profit/len(wins)) / (gross_loss/len(losses)):.2f}")

strats = {}
for t in trades:
    s = t["strategy"].split("-")[0]
    strats.setdefault(s, {"count": 0, "pnl": 0.0, "wins": 0})
    strats[s]["count"] += 1
    strats[s]["pnl"] += t["net_pnl"]
    if t["net_pnl"] > 0:
        strats[s]["wins"] += 1

print("\nStrategy Breakdown:")
for s, d in strats.items():
    winrate = (d["wins"] / d["count"]) * 100.0
    print(f"  {s}: {d['count']} trades, WinRate {winrate:.1f}%, Net PnL: ${d['pnl']:+.2f}")

reasons = {}
for t in trades:
    r = t["reason"].split(":")[0]
    reasons.setdefault(r, {"count": 0, "pnl": 0.0})
    reasons[r]["count"] += 1
    reasons[r]["pnl"] += t["net_pnl"]

print("\nExit Reason Breakdown:")
for r, d in reasons.items():
    print(f"  {r}: {d['count']} trades, Net PnL: ${d['pnl']:+.2f}")

coins = {}
for t in trades:
    c = t["coin"]
    coins.setdefault(c, {"count": 0, "pnl": 0.0, "wins": 0})
    coins[c]["count"] += 1
    coins[c]["pnl"] += t["net_pnl"]
    if t["net_pnl"] > 0:
        coins[c]["wins"] += 1

print("\nCoin Breakdown:")
for c, d in sorted(coins.items(), key=lambda x: x[1]["pnl"], reverse=True):
    winrate = (d["wins"] / d["count"]) * 100.0
    print(f"  {c:10s}: {d['count']} trades, WinRate {winrate:4.1f}%, Net PnL: ${d['pnl']:+6.2f}")
