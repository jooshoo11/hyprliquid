import json
import os
from datetime import datetime

with open("reports/session_trades.json") as f:
    trades = json.load(f)

cleaned = []
skip_indices = set()

for i, t in enumerate(trades):
    if i in skip_indices:
        continue
    for j in range(i + 1, len(trades)):
        t_next = trades[j]
        if t_next["coin"] == t["coin"]:
            dt1 = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
            dt2 = datetime.fromisoformat(t_next["timestamp"].replace("Z", "+00:00"))
            if (dt2 - dt1).total_seconds() <= 180:
                skip_indices.add(j)
            else:
                break
        else:
            break
    cleaned.append(t)

# Save deduplicated session_trades.json
with open("reports/session_trades.json", "w") as f:
    json.dump(cleaned, f, indent=2)

total_net = sum(t["net_pnl"] for t in cleaned)
total_gross = sum(t.get("gross_pnl", t["net_pnl"]) for t in cleaned)
total_fees = sum(t.get("fees", 0.0) for t in cleaned)
wins = sum(1 for t in cleaned if t["net_pnl"] > 0)

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

print("Successfully deduplicated and updated reports and paper_state.json!")
