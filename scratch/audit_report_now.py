import urllib.request
import json
import time

try:
    resp = urllib.request.urlopen("http://localhost:8000/api/state", timeout=5)
    data = json.loads(resp.read().decode())
except Exception as e:
    print(f"Error fetching /api/state: {e}")
    exit(1)

print("=" * 60)
print("LIVE SYSTEM AUDIT AT", time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
print("=" * 60)

equity = data.get("equity", 100.0)
cash = data.get("cash_balance", 100.0)
net_unrealized = data.get("net_unrealized", 0.0)
notional = data.get("notional_exposure", 0.0)
roi = data.get("roi_pct", 0.0)

print(f"Total Equity:         ${equity:.2f}")
print(f"Cash Balance:         ${cash:.2f}")
print(f"Net Unrealized PnL:   {net_unrealized:+.2f} ({roi:+.2f}%)")
print(f"Notional Exposure:    ${notional:.2f}")
print(f"Total Taker Fees:     ${data.get('total_fees_paid', 0.0):.4f}")
print(f"Est Funding Carry:    ${data.get('est_funding_carry', 0.0):.2f}")
print(f"Circuit Breaker:      {data.get('circuit_breaker', 'NORMAL')}")

regime = data.get("market_regime") or {}
print(f"\n--- MARKET REGIME ENGINE ---")
print(f"Active Regime:        {regime.get('regime')}")
print(f"Market Breadth:       {regime.get('breadth_pct')}% green")
print(f"Average 24h Change:   {regime.get('avg_change_24h'):+.2f}%")
print(f"Average Funding APR:  {regime.get('avg_funding_apr'):+.1f}%")
print(f"Sentiment:            {regime.get('sentiment')}")
print(f"Active Tweaks:        {regime.get('active_tweaks')}")

positions = data.get("positions", [])
print(f"\n--- ACTIVE TRADES ({len(positions)}) ---")
for p in positions:
    coin = p.get("coin")
    side = p.get("side")
    size = p.get("size")
    entry = p.get("entry_price")
    mark = p.get("mark_price")
    pnl = p.get("unrealized_pnl", 0.0)
    roi_p = p.get("roi_pct", 0.0)
    strat = p.get("strategy")
    print(f"  • {coin:6} [{side:5}] {strat:18}: size {size:<8} @ ${entry:<10.4f} | Mark: ${mark:<10.4f} | PnL: {pnl:+.2f} ({roi_p:+.2f}%)")

tm = data.get("trade_manager") or {}
tm_pos = tm.get("positions", [])
print(f"\n--- TRADE MANAGER REAL-TIME SENTRY ({len(tm_pos)}) ---")
for tp in tm_pos:
    coin = tp.get("coin")
    roi_val = tp.get("roi_pct", 0.0)
    peak = tp.get("peak_roi_pct", 0.0)
    stop = tp.get("stop_price")
    ratchet = tp.get("breakeven_triggered")
    dur = tp.get("duration_seconds", 0.0)
    stop_str = f"${stop:.4f}" if stop else "None"
    print(f"  • {coin:6}: ROI {roi_val:+.2f}% (Peak {peak:+.2f}%) | Stop: {stop_str:<10} | Ratchet: {str(ratchet):<5} | Duration: {dur/60:.1f}m")

print(f"\nNext Prospector Scan: {data.get('next_prospector_scan')}")
print(f"Last Watchdog Audit:  {data.get('last_watchdog_audit')}")
