import json
import os
from datetime import datetime

with open("reports/session_trades.json") as f:
    trades = json.load(f)

cleaned = []
remnants = []

for i, t in enumerate(trades):
    is_remnant = False
    if cleaned:
        prev = cleaned[-1]
        # Same coin, same side, same entry price = remnant close of the same trade
        if (
            t["coin"] == prev["coin"]
            and t["side"] == prev["side"]
            and abs(t["entry"] - prev["entry"]) < 1e-4
        ):
            is_remnant = True

    if is_remnant:
        remnants.append(t)
    else:
        cleaned.append(t)

print(f"Total Original Trades: {len(trades)}")
print(f"Remnants Removed: {len(remnants)}")
print(f"Cleaned Trades: {len(cleaned)}")

total_net = sum(t["net_pnl"] for t in cleaned)
total_gross = sum(t.get("gross_pnl", t["net_pnl"]) for t in cleaned)
total_fees = sum(t.get("fees", 0.0) for t in cleaned)
wins = sum(1 for t in cleaned if t["net_pnl"] > 0)
win_rate = (wins / len(cleaned)) * 100

print(f"Cleaned Realized P&L: ${total_net:+.2f}")
print(f"Cleaned Total Fees: ${total_fees:.2f}")
print(f"Cleaned Cash Balance: ${100.0 + total_net:.2f}")
print(f"Cleaned Win Rate: {wins}/{len(cleaned)} ({win_rate:.1f}%)")

# Save cleaned session_trades.json
with open("reports/session_trades.json", "w") as f:
    json.dump(cleaned, f, indent=2)

# Rebuild daily_pnl.md
lines = [
    "# 📈 Daily P&L Journal & Session Analytics\n",
    "\n",
    "**Session Start**: `2026-09-29T17:25:00Z`  \n",
    "**Starting Equity**: `$100.00`  \n",
    f"**Current Realized P&L**: `${total_net:+.2f}`  \n",
    f"**Total Fees Paid**: `${total_fees:.2f}`  \n",
    f"**Realized Cash Balance**: `${100.0 + total_net:.2f}`  \n",
    "\n",
    "## Executed Trades\n",
    "\n",
    "| Timestamp | Coin | Side | Size | Entry Px | Exit Px | Duration | Gross P&L | Fees ($) | Net P&L | ROI (%) | Strategy | Reason |\n",
    "|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|\n"
]

for t in cleaned:
    ts = t["timestamp"].replace("T", " ").replace("Z", "")
    coin = t["coin"]
    side = t["side"]
    sz = t["size"]
    entry_val = t["entry"]
    exit_val = t["exit"]
    entry_str = f"${entry_val:,.4f}"
    exit_str = f"${exit_val:,.4f}"
    dur = t["duration"]
    gross = t.get("gross_pnl", t["net_pnl"])
    gross_str = f"+${gross:,.2f}" if gross >= 0 else f"-${abs(gross):,.2f}"
    fees_val = t.get("fees", 0.0)
    fees_str = f"${fees_val:,.2f}"
    net = t["net_pnl"]
    net_str = f"+${net:,.2f}" if net >= 0 else f"-${abs(net):,.2f}"
    roi = t["roi"]
    roi_str = f"{roi:+.2f}%"
    strat = t["strategy"]
    reason = t["reason"]
    lines.append(f"| {ts} | {coin} | {side} | {sz} | {entry_str} | {exit_str} | {dur} | {gross_str} | {fees_str} | {net_str} | {roi_str} | {strat} | {reason} |\n")

with open("reports/daily_pnl.md", "w") as f:
    f.writelines(lines)

# Update paper_state.json
if os.path.exists("bridge/paper_state.json"):
    with open("bridge/paper_state.json") as f:
        ps = json.load(f)

    unrealized = sum(p.get("unrealized_pnl", 0.0) for p in ps.get("open_positions", []))
    ps["realized_pnl"] = round(total_net, 2)
    ps["equity"] = round(100.0 + total_net + unrealized, 2)
    ps["total_trades"] = len(cleaned)

    with open("bridge/paper_state.json", "w") as f:
        json.dump(ps, f, indent=2)

print("Saved cleaned reports and paper_state.json successfully!")
