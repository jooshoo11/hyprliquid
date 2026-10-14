import os, glob, subprocess

out = ["# FULL SYSTEM AUDIT V2 SNAPSHOT (ENGINE + BACKTEST FUNNEL + GROQ + WEB COCKPIT)\n"]

def add_section(title, content):
    sep = "=" * 80
    out.append(f"\n{sep}\n## {title}\n{sep}\n{content}\n")

# 1. Git Status, Diff Stat & Pytest Summary
for cmd in ["git status -s", "git diff --stat", ".venv/bin/pytest -q --tb=short"]:
    try:
        res = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=90)
        add_section(f"COMMAND: {cmd}", f"```text\n{res}\n```")
    except Exception as e:
        out_str = getattr(e, "output", str(e))
        add_section(f"COMMAND: {cmd} (WARN/FAIL)", f"```text\n{out_str}\n```")

# 2. Live Config, Bridge State & Reports
runtime_files = (
    sorted(glob.glob("config/*.json")) +
    sorted(glob.glob("bridge/*.json")) +
    [
        "reports/session_trades.json",
        "reports/daily_pnl.md",
        "reports/performance_analytics.md",
        "reports/walk_forward_report.md",
        "reports/missed_opportunities.md",
    ]
)
for path in runtime_files:
    if os.path.exists(path):
        content = open(path, errors="replace").read()
        if len(content) > 20000:
            content = content[:4000] + "\n... [TRUNCATED MIDDLE] ...\n" + content[-16000:]
        add_section(f"STATE/CONFIG FILE: {path}", f"```\n{content}\n```")

# 3. Runtime Logs (Last 200 lines)
for log_path in ["bridge/sentinel.log", "artifacts/burn_in.log"]:
    if os.path.exists(log_path):
        lines = open(log_path, errors="replace").readlines()
        add_section(f"LOG TAIL (Last 200 lines): {log_path}", "```text\n" + "".join(lines[-200:]) + "\n```")

# 4. Complete Core Source Tree (Engine, Risk, Scanner, Backtest, Optimization, Strategies, Web)
source_files = [
    "run.py",
    "src/engine/unified_engine.py",
    "src/risk/portfolio_guard.py",
    "src/risk/trade_manager.py",
    "src/risk/regime_manager.py",
    "src/utils/llm_client.py",
    "src/utils/instruments.py",
    "src/scanner/ai_studio_prospector.py",
    "src/scanner/analytics_pipeline.py",
    "src/scanner/catalog_builder.py",
    "src/backtest/run_backtest.py",
    "src/backtest/walk_forward_runner.py",
    "src/optimization/optimize_suite.py",
    "src/strategies/high_conviction_momentum.py",
    "src/strategies/orderbook_scalp.py",
    "src/strategies/vwap_momentum.py",
    "src/strategies/continuation.py",
    "src/strategies/funding_fade.py",
    "src/web/server.py",
    "src/web/index.html",
]
for path in source_files:
    if os.path.exists(path):
        ext = "html" if path.endswith(".html") else "py"
        add_section(f"SOURCE: {path}", f"```{ext}\n{open(path, errors='replace').read()}\n```")

with open("full_system_audit_v2.txt", "w") as f:
    f.write("".join(out))

print(f"✅ Created full_system_audit_v2.txt ({os.path.getsize('full_system_audit_v2.txt') / 1024:.1f} KB)")
