#!/usr/bin/env python3
"""Analyze backtest trade distribution."""
import json
from pathlib import Path

report_path = Path('results/backtest_report.json')
if not report_path.exists():
    print("No backtest report found. Run backtest first.")
    exit(1)

with open(report_path) as f:
    data = json.load(f)

trades = data['trades']
wins = [t for t in trades if t['pnl'] > 0]
losses = [t for t in trades if t['pnl'] <= 0]

print("=" * 60)
print("TRADE ANALYSIS")
print("=" * 60)
print(f"Total trades: {len(trades)}")
print(f"Win count: {len(wins)} ({len(wins)/len(trades)*100:.1f}%)")
print(f"Loss count: {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
print()
print(f"Average win:  ${sum(t['pnl'] for t in wins)/len(wins):.2f}")
print(f"Average loss: ${sum(t['pnl'] for t in losses)/len(losses):.2f}")
print()

# Win/Loss ratio
avg_win = sum(t['pnl'] for t in wins)/len(wins)
avg_loss = abs(sum(t['pnl'] for t in losses)/len(losses))
ratio = avg_win / avg_loss
print(f"Win/Loss ratio: {ratio:.2f} (should be > 1.5 for profitability)")
print()

# Expectancy
win_rate = len(wins) / len(trades)
expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
print(f"Expectancy per trade: ${expectancy:.2f}")
print(f"Expectancy as % of $10k: {expectancy/100:.2f}%")
print()

# Largest wins and losses
trades_sorted = sorted(trades, key=lambda x: x['pnl'], reverse=True)
print("Top 5 wins:")
for t in trades_sorted[:5]:
    print(f"  {t['symbol']} {t['side']}: +${t['pnl']:.2f}")

print("\nTop 5 losses:")
for t in trades_sorted[-5:]:
    print(f"  {t['symbol']} {t['side']}: ${t['pnl']:.2f}")

print("\n" + "=" * 60)
print("DIAGNOSIS:")
print("=" * 60)

if ratio < 1.0:
    print("❌ PROBLEM: Average loss > Average win")
    print("   → Try wider take-profit or tighter stop-loss")
    print("   → Current TP:SL ratio might be too low")
elif win_rate < 0.35:
    print("❌ PROBLEM: Win rate too low (< 35%)")
    print("   → Signal quality issue - EMA crossover not selective enough")
    print("   → Try adding filters (ADX, volume, time-of-day)")
    print("   → Or try different EMA periods (slower = fewer false signals)")
elif win_rate > 0.5 and ratio < 1.3:
    print("⚠️  ISSUE: Good win rate but poor R:R")
    print("   → You're winning often but losses wipe out gains")
    print("   → Let winners run (wider TP) or cut losers faster")
else:
    print("✓ Metrics look reasonable - might be fees/slippage issue")
    print("  → Try reducing trade frequency")
    print("  → Test on different time period")

print("\nNext step: Run parameter_sweep.py to find better settings")