import os
import glob
import subprocess

out = ["# POST-BURN-IN & PATCH VERIFICATION SNAPSHOT\n"]

def add_section(title, content):
    sep = "=" * 80
    out.append(f"\n{sep}\n## {title}\n{sep}\n{content}\n")

# 1. Git Status & Diff Stat
for cmd in ["git status -s", "git diff --stat"]:
    try:
        res = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)
        add_section(f"GIT: {cmd}", f"```text\n{res}\n```")
    except Exception as e:
        pass

# 2. Live Bridge Telemetry & Reports
runtime_files = (
    sorted(glob.glob("bridge/*.json")) +
    [
        "reports/session_trades.json",
        "reports/daily_pnl.md",
        "reports/performance_analytics.md",
        "reports/missed_opportunities.md",
        "reports/decision_journal.jsonl",
    ]
)
for path in runtime_files:
    if os.path.exists(path):
        content = open(path, errors="replace").read()
        if len(content) > 25000:
            content = content[:5000] + "\n... [TRUNCATED MIDDLE] ...\n" + content[-20000:]
        add_section(f"RUNTIME FILE: {path}", f"```\n{content}\n```")

# 3. Runtime Logs (Last 250 lines)
for log_path in ["bridge/sentinel.log", "artifacts/burn_in.log"]:
    if os.path.exists(log_path):
        lines = open(log_path, errors="replace").readlines()
        add_section(f"LOG TAIL (Last 250 lines): {log_path}", "```text\n" + "".join(lines[-250:]) + "\n```")

# 4. Patched Source Files
patched_files = [
    "src/engine/unified_engine.py",
    "src/risk/portfolio_guard.py",
    "src/risk/trade_manager.py",
    "src/strategies/funding_arbitrage.py",
    "src/strategies/orderbook_scalp.py",
    "src/strategies/vwap_momentum.py",
    "src/strategies/liquidation_absorber.py",
    "src/strategies/high_conviction_momentum.py",
    "src/scanner/analytics_pipeline.py",
    "src/scanner/ai_studio_prospector.py",
    "src/tui/data_provider.py",
]
for path in patched_files:
    if os.path.exists(path):
        add_section(f"PATCHED SOURCE: {path}", f"```py\n{open(path, errors='replace').read()}\n```")

with open("post_burnin_audit.txt", "w") as f:
    f.write("".join(out))

print(f"✅ Created post_burnin_audit.txt ({os.path.getsize('post_burnin_audit.txt') / 1024:.1f} KB)")
