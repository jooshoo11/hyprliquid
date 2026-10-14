"""
scripts/reset_runtime_state.py
Cleanly wipes pre-patch artifacts, resets the $200 paper baseline,
and archives bugged session trades so dynamic allocators start fresh.
"""
import os
import json
import shutil
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
REPORTS_DIR = os.path.join(REPO_ROOT, "reports")
ARCHIVE_DIR = os.path.join(REPORTS_DIR, "archive", f"session_pre_audit_{int(time.time())}")

os.makedirs(BRIDGE_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# 1. Archive contaminated historical session trades, journals & opportunities
for report_file in ["session_trades.json", "daily_pnl.md", "decision_journal.jsonl", "missed_opportunities.md"]:
    src = os.path.join(REPORTS_DIR, report_file)
    if os.path.exists(src):
        dst = os.path.join(ARCHIVE_DIR, report_file)
        shutil.move(src, dst)
        print(f"📦 Archived {report_file} -> {os.path.relpath(dst, REPO_ROOT)}")

# Create clean empty replacements in reports/
with open(os.path.join(REPORTS_DIR, "session_trades.json"), "w") as f:
    json.dump([], f)
with open(os.path.join(REPORTS_DIR, "daily_pnl.md"), "w") as f:
    f.write("# Daily PnL & Trade Journal\n\n| Date/Time (UTC) | Coin | Side | Size | Entry Px | Exit Px | Duration | Gross PnL ($) | Fees ($) | Net PnL ($) | ROI (%) | Strategy | Reason |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
with open(os.path.join(REPORTS_DIR, "decision_journal.jsonl"), "w") as f:
    f.write("")
with open(os.path.join(REPORTS_DIR, "missed_opportunities.md"), "w") as f:
    f.write("# Missed Opportunities Log\n\n| Date/Time (UTC) | Coin | Strategy | Reason | Key Metrics |\n|---|---|---|---|---|\n")

# 2. Reset paper state to canonical $200 baseline
clean_paper_state = {
    "equity": 200.0,
    "cash_balance": 200.0,
    "realized_pnl": 0.0,
    "positions": [],
    "open_positions": [],
    "starting_balance": 200.0,
    "total_trades": 0,
    "session_start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
with open(os.path.join(BRIDGE_DIR, "paper_state.json"), "w") as f:
    json.dump(clean_paper_state, f, indent=2)

# 3. Clear active trades and command queues
with open(os.path.join(BRIDGE_DIR, "active_trades.json"), "w") as f:
    json.dump({
        "timestamp": time.time(),
        "equity": 200.0,
        "cash_balance": 200.0,
        "realized_pnl": 0.0,
        "positions": []
    }, f, indent=2)

with open(os.path.join(BRIDGE_DIR, "ai_commands.json"), "w") as f:
    json.dump([], f)

print("✅ State reset complete: Baseline clean at $200.00, queues cleared.")