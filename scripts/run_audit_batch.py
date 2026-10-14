"""
scripts/run_audit_batch.py
Runs N audit iterations spaced by delay_sec.
Returns structured JSON array of cycle results.
"""

import sys
import os
import json
import time
import argparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from scripts.subagent_stream_audit import audit_cycle

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--delay", type=float, default=20.0)
    parser.add_argument("--start-idx", type=int, default=1)
    args = parser.parse_args()

    results = []
    for i in range(args.iterations):
        cycle_num = args.start_idx + i
        if i > 0:
            time.sleep(args.delay)
        res = audit_cycle()
        res["cycle_num"] = cycle_num
        results.append(res)
        print(f"Cycle {cycle_num} complete: {res['audit_line']}", flush=True)
        if res["has_traceback"]:
            print(f"ALERT: Traceback detected in cycle {cycle_num}!", flush=True)
            break

    print("---END_BATCH---")
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
