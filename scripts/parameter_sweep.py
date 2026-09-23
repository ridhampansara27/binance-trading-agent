#!/usr/bin/env python3
"""
Systematically test different parameter combinations to find profitable settings.
Tests multiple EMA periods and stop-loss/take-profit ratios.
"""
import asyncio
from pathlib import Path
from datetime import datetime

from trend_scalper.config import load_config, StrategyConfig, TradingConfig
from trend_scalper.backtester import Backtester
from trend_scalper.storage import load_klines_parquet


async def sweep():
    print("=" * 80)
    print("PARAMETER SWEEP - Finding optimal strategy settings")
    print("=" * 80)
    print()
    
    # Load base config
    base_cfg = load_config('config/settings.yaml')
    
    # Load data once
    print("Loading data...")
    data = {}
    for sym in ['BTCUSDT', 'ETHUSDT']:
        for tf in ['1m', '5m']:
            key = f'{sym}_{tf}'
            path = Path(f'data/klines/{sym.lower()}_{tf}.parquet')
            if path.exists():
                data[key] = load_klines_parquet(path)
                print(f"  ✓ Loaded {key}: {len(data[key])} rows")
    
    if not data:
        print("❌ No data found! Run download_klines.py first.")
        return
    
    print()
    
    # Test configurations: (ema_fast, ema_slow, stop_loss%, take_profit%)
    test_configs = [
        # Original
        (9, 21, 0.003, 0.006, "Original"),
        
        # Slower EMAs (fewer false signals)
        (12, 26, 0.003, 0.006, "Classic 12/26"),
        (15, 50, 0.003, 0.006, "Slow 15/50"),
        (20, 50, 0.003, 0.006, "Very Slow 20/50"),
        (10, 30, 0.003, 0.006, "Medium 10/30"),
        
        # Wider stops (let trades breathe)
        (12, 26, 0.005, 0.010, "12/26 Wide SL/TP"),
        (15, 50, 0.005, 0.010, "15/50 Wide SL/TP"),
        (9, 21, 0.005, 0.010, "Original Wide SL/TP"),
        
        # Better R:R ratio (1:3)
        (12, 26, 0.004, 0.012, "12/26 R:R=3"),
        (15, 50, 0.004, 0.012, "15/50 R:R=3"),
        (9, 21, 0.004, 0.012, "Original R:R=3"),
        
        # Tighter stops (cut losses faster)
        (12, 26, 0.002, 0.008, "12/26 Tight SL"),
        (15, 50, 0.002, 0.008, "15/50 Tight SL"),
        
        # Aggressive (higher risk per trade)
        (12, 26, 0.006, 0.015, "12/26 Aggressive"),
        (20, 50, 0.006, 0.018, "20/50 Aggressive"),
    ]
    
    results = []
    
    print(f"\nTesting {len(test_configs)} configurations...")
    print("-" * 80)
    
    for ema_f, ema_s, sl_pct, tp_pct, name in test_configs:
        # Create modified config
        from copy import deepcopy
        cfg = deepcopy(base_cfg)
        cfg.strategy.ema_fast = ema_f
        cfg.strategy.ema_slow = ema_s
        cfg.trading.stop_loss_pct = sl_pct
        cfg.trading.take_profit_pct = tp_pct
        
        # Run backtest
        backtester = Backtester(cfg, data, initial_equity=10000.0)
        result = backtester.run()
        
        # Store results
        results.append({
            'name': name,
            'ema_fast': ema_f,
            'ema_slow': ema_s,
            'stop_loss': sl_pct,
            'take_profit': tp_pct,
            'return': result.total_return,
            'return_pct': result.total_return * 100,
            'win_rate': result.win_rate,
            'win_rate_pct': result.win_rate * 100,
            'sharpe': result.sharpe_ratio,
            'max_dd': result.max_drawdown,
            'max_dd_pct': result.max_drawdown * 100,
            'total_trades': result.total_trades,
            'expectancy': result.expectancy,
        })
        
        # Color code based on return
        if result.total_return > 0:
            status = "✅ PROFIT"
        elif result.total_return > -0.20:
            status = "⚠️  -Loss"
        else:
            status = "❌ BigLoss"
        
        print(f"{name:25} | Return: {result.total_return:7.2%} | "
              f"Win: {result.win_rate:5.1%} | "
              f"Sharpe: {result.sharpe_ratio:6.2f} | "
              f"Trades: {result.total_trades:3} | {status}")
    
    print("-" * 80)
    print()
    
    # Sort by different metrics
    print("=" * 80)
    print("TOP 5 BY RETURN:")
    print("=" * 80)
    by_return = sorted(results, key=lambda x: x['return'], reverse=True)[:5]
    for i, r in enumerate(by_return, 1):
        print(f"{i}. {r['name']:25} | Return: {r['return_pct']:7.2f}% | "
              f"Win: {r['win_rate_pct']:5.1f}% | DD: {r['max_dd_pct']:6.1f}%")
    
    print()
    print("=" * 80)
    print("TOP 5 BY SHARPE RATIO (risk-adjusted):")
    print("=" * 80)
    by_sharpe = sorted(results, key=lambda x: x['sharpe'], reverse=True)[:5]
    for i, r in enumerate(by_sharpe, 1):
        print(f"{i}. {r['name']:25} | Sharpe: {r['sharpe']:7.2f} | "
              f"Return: {r['return_pct']:6.2f}% | Win: {r['win_rate_pct']:5.1f}%")
    
    print()
    print("=" * 80)
    print("TOP 5 BY WIN RATE:")
    print("=" * 80)
    by_winrate = sorted(results, key=lambda x: x['win_rate'], reverse=True)[:5]
    for i, r in enumerate(by_winrate, 1):
        print(f"{i}. {r['name']:25} | Win: {r['win_rate_pct']:5.1f}% | "
              f"Return: {r['return_pct']:6.2f}% | Trades: {r['total_trades']:3}")
    
    print()
    print("=" * 80)
    print("RECOMMENDATION:")
    print("=" * 80)
    
    # Find best overall (positive return + decent sharpe + reasonable DD)
    good_ones = [r for r in results if r['return'] > 0 and r['sharpe'] > 0 and r['max_dd'] > -0.30]
    
    if good_ones:
        best = max(good_ones, key=lambda x: x['sharpe'])
        print(f"✅ Found {len(good_ones)} profitable configs!")
        print()
        print(f"BEST OVERALL: {best['name']}")
        print(f"  EMA: {best['ema_fast']}/{best['ema_slow']}")
        print(f"  Stop: {best['stop_loss']:.3f} ({best['stop_loss']*100:.2f}%)")
        print(f"  Take Profit: {best['take_profit']:.3f} ({best['take_profit']*100:.2f}%)")
        print(f"  Return: {best['return_pct']:.1f}%")
        print(f"  Sharpe: {best['sharpe']:.2f}")
        print(f"  Win Rate: {best['win_rate_pct']:.1f}%")
        print(f"  Max Drawdown: {best['max_dd_pct']:.1f}%")
        print()
        print(f"Update config/settings.yaml with these values and re-run full backtest!")
    else:
        print("❌ No configs were profitable in this test period.")
        print()
        print("Next steps:")
        print("  1. Test on different time period (market regime)")
        print("  2. Add filters (time-of-day, ADX, volume)")
        print("  3. Try mean reversion strategy instead")
        print()
        # Show least bad option
        least_bad = max(results, key=lambda x: x['sharpe'])
        print(f"Best available (still negative): {least_bad['name']}")
        print(f"  Sharpe: {least_bad['sharpe']:.2f}, Return: {least_bad['return_pct']:.1f}%")
    
    # Save full results
    output_path = Path('results/parameter_sweep_results.json')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    import json
    with open(output_path, 'w') as f:
        json.dump({
            'timestamp': datetime.utcnow().isoformat(),
            'results': results,
            'best_by_return': by_return[0] if by_return else None,
            'best_by_sharpe': by_sharpe[0] if by_sharpe else None,
            'best_by_winrate': by_winrate[0] if by_winrate else None,
        }, f, indent=2)
    
    print()
    print(f"Full results saved to: {output_path}")
    print("=" * 80)


if __name__ == '__main__':
    asyncio.run(sweep())
