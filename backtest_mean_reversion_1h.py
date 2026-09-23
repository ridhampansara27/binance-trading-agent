#!/usr/bin/env python3
"""
Backtest Mean Reversion on 1h data only.
"""
import asyncio
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Any

import pandas as pd
import ta.momentum as ta_momentum
import ta.volatility as ta_volatility

from trend_scalper.config import load_config
from trend_scalper.storage import load_klines_parquet, save_backtest_result
from trend_scalper.models import Signal, Side, Trade


@dataclass
class BacktestResult:
    total_return: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    expectancy: float = 0.0
    equity_curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class MeanReversionBacktester:
    def __init__(self, config, data: dict[str, Any], initial_equity: float = 10000.0):
        self.config = config
        self.data = data
        self.initial_equity = initial_equity
        
        self.rsi_period = config.strategy.rsi_period
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.bb_window = 20
        self.bb_std = 2.0
        self.stop_loss_pct = 0.02
        self.take_profit_pct = 0.05
        
        self.taker_fee = config.trading.taker_fee
        self.slippage_bps = config.trading.slippage_bps
        self.risk_per_trade = config.trading.risk_per_trade
        
        self.equity = initial_equity
        self.positions = {}
        self.trades = []
        self.equity_curve = [initial_equity]
    
    def _add_indicators(self, df):
        df = df.copy()
        df['rsi'] = ta_momentum.rsi(df['close'], window=self.rsi_period)
        
        bb = ta_volatility.BollingerBands(
            close=df['close'],
            window=self.bb_window,
            window_dev=self.bb_std
        )
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_middle'] = bb.bollinger_mavg()
        df['bb_lower'] = bb.bollinger_lband()
        
        return df
    
    def _generate_signal(self, df, symbol, current_price):
        if df.empty or len(df) < self.bb_window + 5:
            return None

        df = self._add_indicators(df)
        
        rsi = df['rsi'].iloc[-1]
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        bb_middle = df['bb_middle'].iloc[-1]

        side = None

        if rsi < self.rsi_oversold and current_price < bb_lower:
            side = Side.LONG
        elif rsi > self.rsi_overbought and current_price > bb_upper:
            side = Side.SHORT

        if side is None:
            return None

        if side == Side.LONG:
            stop_loss = current_price * (1 - self.stop_loss_pct)
            take_profit = bb_middle
        else:
            stop_loss = current_price * (1 + self.stop_loss_pct)
            take_profit = bb_middle

        return Signal(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            side=side,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strength=1.0,
            metadata={
                'rsi': float(rsi),
                'bb_upper': float(bb_upper),
                'bb_middle': float(bb_middle),
                'bb_lower': float(bb_lower),
            },
        )
    
    def run(self):
        symbols = list(self.data.keys())
        if not symbols:
            return self._empty_result()
        
        dfs = {s: self._add_indicators(df) for s, df in self.data.items()}
        
        min_len = min(len(df) for df in dfs.values())
        for s in symbols:
            dfs[s] = dfs[s].iloc[-min_len:].reset_index(drop=True)
        
        in_position = False
        current_symbol = None
        
        for i in range(len(dfs[symbols[0]])):
            ts = dfs[symbols[0]].iloc[i]['timestamp']
            if not isinstance(ts, datetime):
                ts = pd.to_datetime(ts).to_pydatetime()
            
            if not in_position:
                for sym in symbols:
                    row = dfs[sym].iloc[i]
                    price = float(row['close'])
                    
                    signal = self._generate_signal(
                        df=dfs[sym].iloc[:i+1],
                        symbol=sym,
                        current_price=price,
                    )
                    
                    if signal:
                        risk_amount = self.equity * self.risk_per_trade
                        risk_per_unit = abs(signal.entry_price - signal.stop_loss)
                        if risk_per_unit <= 0:
                            risk_per_unit = signal.entry_price * 0.01
                        
                        qty = risk_amount / risk_per_unit
                        
                        entry_price = signal.entry_price * (1 + self.slippage_bps / 10000)
                        entry_fee = entry_price * qty * self.taker_fee
                        
                        self.positions[sym] = {
                            'side': signal.side,
                            'qty': qty,
                            'entry': entry_price,
                            'sl': signal.stop_loss,
                            'tp': signal.take_profit,
                            'fees': entry_fee,
                            'bar_open': i,
                            'metadata': signal.metadata,
                        }
                        in_position = True
                        current_symbol = sym
                        break
            
            if in_position and current_symbol:
                pos = self.positions[current_symbol]
                sym = current_symbol
                price = float(dfs[sym].iloc[i]['close'])
                
                should_exit = False
                reason = ''
                
                if pos['side'] == Side.LONG:
                    if price <= pos['sl']:
                        should_exit = True
                        reason = 'stop_loss'
                    elif price >= pos['tp']:
                        should_exit = True
                        reason = 'take_profit'
                else:
                    if price >= pos['sl']:
                        should_exit = True
                        reason = 'stop_loss'
                    elif price <= pos['tp']:
                        should_exit = True
                        reason = 'take_profit'
                
                if should_exit:
                    exit_price = price * (1 - self.slippage_bps / 10000)
                    exit_fee = exit_price * pos['qty'] * self.taker_fee
                    
                    pnl = (exit_price - pos['entry']) * pos['qty']
                    if pos['side'] == Side.SHORT:
                        pnl = -pnl
                    
                    net_pnl = pnl - pos['fees'] - exit_fee
                    
                    trade = Trade(
                        symbol=sym,
                        side=pos['side'],
                        entry_price=pos['entry'],
                        exit_price=exit_price,
                        quantity=pos['qty'],
                        pnl=net_pnl,
                        fees=pos['fees'] + exit_fee,
                        entry_time=dfs[sym].iloc[pos['bar_open']]['timestamp'],
                        exit_time=ts,
                        metadata={'reason': reason, **pos['metadata']},
                    )
                    self.trades.append(trade)
                    self.equity += net_pnl
                    
                    del self.positions[sym]
                    in_position = False
                    current_symbol = None
            
            self.equity_curve.append(self.equity)
        
        return self._compute_result()
    
    def _empty_result(self):
        return BacktestResult()
    
    def _compute_result(self):
        if not self.trades:
            return BacktestResult(
                total_return=0.0,
                equity_curve=self.equity_curve,
                trades=self.trades,
            )
        
        pnls = [t.pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        
        total_trades = len(self.trades)
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        equity_series = pd.Series(self.equity_curve)
        peak = equity_series.expanding().max()
        drawdown = (equity_series - peak) / peak
        max_drawdown = float(drawdown.min()) if not drawdown.isna().all() else 0.0
        
        returns = equity_series.pct_change().dropna()
        sharpe = float(returns.mean() / returns.std()) * (252 ** 0.5) if returns.std() > 0 else 0.0
        
        downside = returns[returns < 0]
        sortino = (
            float(returns.mean() / downside.std()) * (252 ** 0.5)
            if len(downside) > 1 and downside.std() > 0
            else 0.0
        )
        
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss) if total_trades > 0 else 0.0
        total_return = (equity_series.iloc[-1] - self.initial_equity) / self.initial_equity
        
        return BacktestResult(
            total_return=total_return,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            expectancy=expectancy,
            equity_curve=self.equity_curve,
            trades=self.trades,
            metadata={'initial_equity': self.initial_equity, 'final_equity': equity_series.iloc[-1]},
        )


async def run_backtest(config_path: str, data_dir: str, output: str):
    config = load_config(config_path)
    
    data = {}
    data_path = Path(data_dir)
    
    # Only load 1h data
    for sym in ['BTCUSDT', 'ETHUSDT']:
        file_path = data_path / f'{sym.lower()}_1h.parquet'
        if file_path.exists():
            key = f'{sym}_1h'
            data[key] = load_klines_parquet(file_path)
            print(f"Loaded {key}: {len(data[key])} rows")
    
    if not data:
        print("No 1h data found!")
        return
    
    print("Running mean reversion backtest on 1h data...")
    backtester = MeanReversionBacktester(config, data, initial_equity=10000.0)
    result = backtester.run()
    
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_backtest_result(result, out_path)
    
    print(f"\nBacktest complete:")
    print(f"  Return: {result.total_return:+.1%}")
    print(f"  Win Rate: {result.win_rate:.1%}")
    print(f"  Sharpe: {result.sharpe_ratio:.2f}")
    print(f"  Max DD: {result.max_drawdown:+.1%}")
    print(f"  Trades: {result.total_trades}")
    print(f"  Avg Win: ${result.avg_win:.2f}")
    print(f"  Avg Loss: ${result.avg_loss:.2f}")
    print(f"  Profit Factor: {result.profit_factor:.2f}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config/settings.yaml')
    parser.add_argument('--data-dir', default='data/klines')
    parser.add_argument('--output', default='results/mean_reversion_1h_report.json')
    args = parser.parse_args()
    
    asyncio.run(run_backtest(args.config, args.data_dir, args.output))


if __name__ == '__main__':
    main()