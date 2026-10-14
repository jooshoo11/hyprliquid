import os
import glob
import subprocess

out = ["# FULL SYSTEM AUDIT V3 SNAPSHOT (POST-V2 + 2-SLOT $200 SIZING + BOT TERMINAL + TP/SL MATH)\n"]

def add_section(title, content):
    border = "=" * 80
    out.append(f"\n{border}\n## {title}\n{border}\n{content}\n")

# 1. Git Status, Diff Stat & Pytest Summary (Using .venv/bin/pytest)
for cmd in ["git status -s", "git diff --stat", ".venv/bin/pytest -q --tb=short"]:
    try:
        res = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=90)
        add_section(f"COMMAND: {cmd}", f"```text\n{res}\n```")
    except Exception as e:
        out_str = getattr(e, "output", str(e))
        add_section(f"COMMAND: {cmd} (WARN/FAIL)", f"```text\n{out_str}\n```")

# 2. Agent Rules & Subagent Blueprints Created Since V2
agent_files = sorted(glob.glob(".agents/rules/*.md")) + sorted(glob.glob(".agents/agents/*.md"))
for path in agent_files:
    if os.path.exists(path):
        add_section(f"AGENT BLUEPRINT: {path}", f"```markdown\n{open(path, errors='replace').read()}\n```")

# 3. Live Config, Bridge State & Reports
runtime_files = (
    sorted(glob.glob("config/*.json")) +
    sorted(glob.glob("bridge/*.json")) +
    [
        "reports/session_trades.json",
        "reports/daily_pnl.md",
        "reports/performance_analytics.md",
        "reports/walk_forward_report.md",
        "reports/missed_opportunities.md",
        "reports/decision_journal.jsonl",
    ]
)
for path in runtime_files:
    if os.path.exists(path):
        content = open(path, errors="replace").read()
        if len(content) > 25000:
            content = content[:5000] + "\n... [TRUNCATED MIDDLE] ...\n" + content[-20000:]
        add_section(f"STATE/CONFIG FILE: {path}", f"```\n{content}\n```")

# 4. Runtime Logs (Last 250 lines)
for log_path in ["bridge/sentinel.log", "artifacts/burn_in.log", "logs/trading.log"]:
    if os.path.exists(log_path):
        lines = open(log_path, errors="replace").readlines()
        add_section(f"LOG TAIL (Last 250 lines): {log_path}", "```text\n" + "".join(lines[-250:]) + "\n```")

# 5. Complete Core Source Tree (Including New Bot Dashboards, Execution Adapters & Scripts)
source_files = [
    "run.py",
    "run_15m_bot.py",
    "run_15m_engine.py",
    "scripts/reset_runtime_state.py",
    "src/engine/unified_engine.py",
    "src/risk/portfolio_guard.py",
    "src/risk/trade_manager.py",
    "src/risk/regime_manager.py",
    "src/risk/performance_analytics.py",
    "src/risk/fallback_engine.py",
    "src/utils/llm_client.py",
    "src/utils/instruments.py",
    "src/scanner/ai_studio_prospector.py",
    "src/scanner/analytics_pipeline.py",
    "src/scanner/catalog_builder.py",
    "src/backtest/run_backtest.py",
    "src/backtest/walk_forward_runner.py",
    "src/optimization/optimize_suite.py",
    "src/optimization/optimize_continuation.py",
    "src/optimization/vbt_screener.py",
    "src/strategies/high_conviction_momentum.py",
    "src/strategies/orderbook_scalp.py",
    "src/strategies/vwap_momentum.py",
    "src/strategies/continuation.py",
    "src/strategies/funding_fade.py",
    "src/strategies/funding_arbitrage.py",
    "src/strategies/liquidation_absorber.py",
    "src/web/server.py",
    "src/web/index.html",
    "src/web/templates/bot_terminal.html",
    "src/web/templates/bot_focus.html",
]

# Dynamically catch any additional files in src/execution/, src/web/, or hyperliquid_nautilus/
for extra_glob in ["src/execution/*.py", "src/web/**/*.html", "src/web/**/*.py", "hyperliquid_nautilus/**/*.py"]:
    for p in sorted(glob.glob(extra_glob, recursive=True)):
        if p not in source_files:
            source_files.append(p)

for path in source_files:
    if os.path.exists(path):
        ext = "html" if path.endswith(".html") else "py"
        add_section(f"SOURCE: {path}", f"```{ext}\n{open(path, errors='replace').read()}\n```")

with open("full_system_audit_v3.txt", "w") as f:
    f.write("".join(out))

size_kb = os.path.getsize("full_system_audit_v3.txt") / 1024
print(f"✅ Created full_system_audit_v3.txt ({size_kb:.1f} KB)")
