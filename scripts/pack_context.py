#!/usr/bin/env python3
"""
Codebase Context Packer for 1,000,000 Token LLM Context Windows.
Packs all active code into a clean, structured Markdown context file
excluding vendor packages, binary DBs, lockfiles, and archive logs.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Directories and patterns to skip
EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "catalog/data", "catalog/funding",
    "hyperliquid_nautilus/data", "hyperliquid_nautilus/.venv", "vendor",
    "reports/archive", "logs", ".pytest_cache", ".aider.tags.cache.v4",
    "assets", "archive", "node_modules"
}

EXCLUDE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".parquet", ".pyc", ".db",
    ".log", ".lock", ".so", ".bin", ".tar", ".zip", ".gz"
}

EXCLUDE_FILES = {
    ".aider.chat.history.md", ".aider.input.history", "cache.db",
    "uv.lock", "poetry.lock", "package-lock.json"
}

# Priority ordering
PRIORITY_DIRS = ["src", "config", "scripts", "tests"]


def estimate_tokens(text: str) -> int:
    """Rough estimation of tokens (~3.7 characters per token for Python/JSON/Markdown)."""
    return int(len(text) / 3.7)


def should_include(file_path: Path) -> bool:
    rel_path = file_path.relative_to(REPO_ROOT)
    rel_str = str(rel_path)

    # Check excluded directories
    for exc in EXCLUDE_DIRS:
        if rel_str.startswith(exc) or f"/{exc}/" in f"/{rel_str}/":
            return False

    # Check excluded files
    if file_path.name in EXCLUDE_FILES or file_path.name.startswith(".aider"):
        return False

    # Check excluded extensions
    if file_path.suffix.lower() in EXCLUDE_EXTENSIONS:
        return False

    # Only include code and config file types
    allowed_exts = {".py", ".html", ".json", ".md", ".sh", ".toml", ".yml", ".yaml", ".ini"}
    return file_path.suffix.lower() in allowed_exts


def pack_codebase(output_file: Path) -> None:
    collected_files = []
    for root, dirs, files in os.walk(REPO_ROOT):
        # Prune excluded dirs in-place
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]

        for file in files:
            fp = Path(root) / file
            if should_include(fp):
                collected_files.append(fp)

    # Sort files: priority dirs first, then alphabetical
    def sort_key(p: Path):
        rel = str(p.relative_to(REPO_ROOT))
        for idx, prefix in enumerate(PRIORITY_DIRS):
            if rel.startswith(prefix):
                return (idx, rel)
        return (len(PRIORITY_DIRS), rel)

    collected_files.sort(key=sort_key)

    total_bytes = 0
    packed_content = []
    header = [
        "# HYPERLIQUID UNIFIED AUTONOMOUS TRADING SYSTEM - CODEBASE CONTEXT",
        f"# Total Core Files: {len(collected_files)}",
        "# Excluded: vendor packages, lockfiles, runtime logs, binary caches\n",
        "## FILE MANIFEST",
    ]
    for fp in collected_files:
        rel = fp.relative_to(REPO_ROOT)
        header.append(f"- `{rel}`")
    header.append("\n" + "=" * 80 + "\n")
    packed_content.append("\n".join(header))

    for fp in collected_files:
        rel = fp.relative_to(REPO_ROOT)
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            ext = fp.suffix.lstrip(".") or "text"
            block = [
                f"\n## FILE: {rel}\n",
                f"```{ext}",
                content,
                "```\n",
            ]
            packed_content.append("\n".join(block))
            total_bytes += len(content)
        except Exception as e:
            print(f"Skipping {rel}: {e}")

    final_text = "".join(packed_content)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(final_text)

    est_tokens = estimate_tokens(final_text)
    print(f"✅ Successfully packed {len(collected_files)} files into: {output_file}")
    print(f"📊 Total Size: {total_bytes / 1024:.1f} KB")
    print(f"🧠 Estimated Tokens: ~{est_tokens:,} tokens")
    pct_window = (est_tokens / 1_000_000) * 100
    print(f"🎯 Window Utilization: {pct_window:.1f}% of a 1,000,000 token context window")
    print(f"🚀 Free Headroom: {1_000_000 - est_tokens:,} tokens remaining for your prompt & reasoning!")


if __name__ == "__main__":
    out = REPO_ROOT / "reports" / "codebase_context.txt"
    pack_codebase(out)
