import json

with open("reports/session_trades.json") as f:
    trades = json.load(f)

# Filter for trades after 11:50 UTC (our daemon deployment)
recent = [t for t in trades if t.get("timestamp", "") >= "2026-09-30T11:50:00Z"]

print(f"Total trades logged after 11:50 UTC: {len(recent)}")

# Categorize into real trades vs remnant duplicates
real_trades = []
remnants = []

for i, t in enumerate(recent):
    # Check if duplicate of previous trade (same coin, same side, same entry)
    is_remnant = False
    if real_trades:
        prev = real_trades[-1]
        if t["coin"] == prev["coin"] and t["side"] == prev["side"] and abs(t["entry"] - prev["entry"]) < 1e-4:
            is_remnant = True
    
    if is_remnant:
        remnants.append(t)
    else:
        real_trades.append(t)

print(f"Real Unique Trades: {len(real_trades)}")
print(f"Remnant Duplicates: {len(remnants)}")

print("\n--- REMNANT DUPLICATES (Phantom Trades) ---")
for r in remnants:
    print(f"Phantom {r['coin']} {r['side']}: sz={r['size']}, net_pnl=${r['net_pnl']:.2f}, ts={r['timestamp']}")

print("\n--- REAL TRADES SUMMARY ---")
by_strat = {}
for t in real_trades:
    s = t["strategy"].split("-")[0]
    by_strat.setdefault(s, []).append(t)

for s, strats in by_strat.items():
    wins = sum(1 for x in strats if x["net_pnl"] > 0)
    pnl = sum(x["net_pnl"] for x in strats)
    print(f"{s:22s}: {wins}/{len(strats)} ({wins/len(strats)*100:.1f}%), Net P&L: ${pnl:+.2f}")

total_net = sum(t["net_pnl"] for t in real_trades)
print(f"\nNet P&L of Real Trades: ${total_net:+.2f}")
