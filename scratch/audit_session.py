import json
from collections import defaultdict

with open('reports/session_trades.json', 'r') as f:
    trades = json.load(f)

print(f"Total Closed Trades: {len(trades)}")

strat_stats = defaultdict(lambda: {'count': 0, 'wins': 0, 'losses': 0, 'gross': 0.0, 'fees': 0.0, 'net': 0.0, 'win_usd': 0.0, 'loss_usd': 0.0})
coin_stats = defaultdict(lambda: {'count': 0, 'net': 0.0, 'gross': 0.0, 'fees': 0.0})
reason_stats = defaultdict(lambda: {'count': 0, 'net': 0.0})
side_stats = defaultdict(lambda: {'count': 0, 'net': 0.0, 'wins': 0})

for t in trades:
    strat = t.get('strategy', 'Unknown').split('-')[0]
    coin = t.get('coin', 'Unknown')
    side = t.get('side', 'Unknown')
    net = float(t.get('net_pnl', t.get('pnl', 0.0)))
    gross = float(t.get('gross_pnl', t.get('pnl', 0.0)))
    fee = float(t.get('fees', 0.0))
    reason = t.get('reason', 'Unknown')
    reason_key = reason.split(':')[0] if ':' in reason else reason
    
    s = strat_stats[strat]
    s['count'] += 1
    s['gross'] += gross
    s['fees'] += fee
    s['net'] += net
    if net > 0:
        s['wins'] += 1
        s['win_usd'] += net
    elif net < 0:
        s['losses'] += 1
        s['loss_usd'] += abs(net)
        
    c = coin_stats[coin]
    c['count'] += 1
    c['net'] += net
    c['gross'] += gross
    c['fees'] += fee
    
    reason_stats[reason_key]['count'] += 1
    reason_stats[reason_key]['net'] += net

    side_stats[side]['count'] += 1
    side_stats[side]['net'] += net
    if net > 0:
        side_stats[side]['wins'] += 1

print("\n=== STRATEGY BREAKDOWN ===")
for strat, s in sorted(strat_stats.items(), key=lambda x: x[1]['net'], reverse=True):
    wr = (s['wins'] / s['count'] * 100) if s['count'] > 0 else 0
    pf = (s['win_usd'] / s['loss_usd']) if s['loss_usd'] > 0 else 999.0
    print(f"{strat:22}: {s['count']:3d} trades | WinRate: {wr:4.1f}% | Gross: ${s['gross']:+6.2f} | Fees: ${s['fees']:5.2f} | Net: ${s['net']:+6.2f} | ProfitFactor: {pf:4.2f}")

print("\n=== LONG VS SHORT BREAKDOWN ===")
for side, sd in side_stats.items():
    wr = (sd['wins'] / sd['count'] * 100) if sd['count'] > 0 else 0
    print(f"{side:10}: {sd['count']:3d} trades | WinRate: {wr:4.1f}% | Net PnL: ${sd['net']:+6.2f}")

print("\n=== TOP 5 WORST COINS ===")
for coin, c in sorted(coin_stats.items(), key=lambda x: x[1]['net'])[:5]:
    print(f"{coin:10}: {c['count']:2d} trades | Gross: ${c['gross']:+6.2f} | Fees: ${c['fees']:5.2f} | Net: ${c['net']:+6.2f}")

print("\n=== TOP 5 BEST COINS ===")
for coin, c in sorted(coin_stats.items(), key=lambda x: x[1]['net'], reverse=True)[:5]:
    print(f"{coin:10}: {c['count']:2d} trades | Gross: ${c['gross']:+6.2f} | Fees: ${c['fees']:5.2f} | Net: ${c['net']:+6.2f}")

print("\n=== EXIT REASON BREAKDOWN ===")
for reason, r in sorted(reason_stats.items(), key=lambda x: x[1]['net']):
    print(f"{reason:35}: {r['count']:3d} trades | Net PnL: ${r['net']:+6.2f}")
