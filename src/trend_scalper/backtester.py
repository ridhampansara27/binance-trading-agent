from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd
import structlog

from .models import Signal, Side, Trade
from .strategy import EMARSIStrategy
from .risk import RiskManager
from .config import AppConfig, StrategyConfig, TradingConfig

logger = structlog.get_logger()


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
    exposure_time: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class Backtester:
    def __init__(
        self,
        app_cfg: AppConfig,
        data: dict[str, pd.DataFrame],
        initial_equity: float = 10000.0,
    ):
        self.app_cfg = app_cfg
        self.data = data
        self.initial_equity = initial_equity

        self.trading_cfg = app_cfg.trading
        self.strategy_cfg = app_cfg.strategy

        self.strategy = EMARSIStrategy(self.strategy_cfg)
        self.risk = RiskManager(self.trading_cfg)
        self.risk.state.equity = initial_equity

        self.positions: dict[str, dict] = {}
        self.trades: list[Trade] = []
        self.equity_curve: list[float] = [initial_equity]

    def _prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        from .indicators import add_trend_indicators, add_momentum_indicators

        df = df.copy()
        df = add_trend_indicators(df, self.strategy_cfg.ema_fast, self.strategy_cfg.ema_slow)
        df = add_momentum_indicators(df, self.strategy_cfg.rsi_period)
        df["volume_ma20"] = df["volume"].rolling(20).mean()
        return df

    def run(self) -> BacktestResult:
        symbols = list(self.data.keys())
        if not symbols:
            return BacktestResult()

        dfs = {s: self._prepare_df(df) for s, df in self.data.items()}
        min_len = min(len(df) for df in dfs.values())
        for s in symbols:
            dfs[s] = dfs[s].iloc[-min_len:].reset_index(drop=True)

        equity = self.initial_equity
        in_position = False

        for i in range(len(dfs[symbols[0]])):
            ts = dfs[symbols[0]].iloc[i]["timestamp"]
            if not isinstance(ts, datetime):
                ts = pd.to_datetime(ts).to_pydatetime()

            current_prices: dict[str, float] = {}
            signals: list[Signal] = []

            for sym in symbols:
                row = dfs[sym].iloc[i]
                price = float(row["close"])
                current_prices[sym] = price

                if in_position:
                    continue

                vol = float(row["volume"])
                vol_ma = float(row["volume_ma20"])
                spike_mult = self.strategy_cfg.volume_spike_mult

                sig = self.strategy.generate_signal(
                    df=dfs[sym].iloc[: i + 1],
                    symbol=sym,
                    current_price=price,
                    volume_spike_mult=spike_mult if vol_ma > 0 else None,
                )
                if sig:
                    signals.append(sig)

            if signals and not in_position and self.risk.can_open_position():
                sig = signals[0]
                risk_amount = equity * self.trading_cfg.risk_per_trade
                risk_per_unit = abs(sig.entry_price - (sig.stop_loss or sig.entry_price * 0.99))
                if risk_per_unit <= 0:
                    risk_per_unit = sig.entry_price * 0.01
                qty = risk_amount / risk_per_unit

                entry_price = sig.entry_price * (1 + self.trading_cfg.slippage_bps / 10000)
                fees = entry_price * qty * self.trading_cfg.taker_fee

                self.positions[sig.symbol] = {
                    "side": sig.side,
                    "qty": qty,
                    "entry": entry_price,
                    "sl": sig.stop_loss,
                    "tp": sig.take_profit,
                    "fees": fees,
                    "bar_open": i,
                }
                in_position = True

            if in_position:
                for sym, pos in list(self.positions.items()):
                    price = current_prices[sym]
                    side = pos["side"]
                    sl = pos["sl"]
                    tp = pos["tp"]
                    hit = False
                    reason = ""

                    if side == Side.LONG:
                        if sl and price <= sl:
                            hit = True
                            reason = "stop_loss"
                        elif tp and price >= tp:
                            hit = True
                            reason = "take_profit"
                    else:
                        if sl and price >= sl:
                            hit = True
                            reason = "stop_loss"
                        elif tp and price <= tp:
                            hit = True
                            reason = "take_profit"

                    if hit:
                        exit_price = price * (1 - self.trading_cfg.slippage_bps / 10000)
                        pnl = (exit_price - pos["entry"]) * pos["qty"]
                        if side == Side.SHORT:
                            pnl = -pnl
                        fees_exit = exit_price * pos["qty"] * self.trading_cfg.taker_fee
                        net_pnl = pnl - pos["fees"] - fees_exit

                        trade = Trade(
                            symbol=sym,
                            side=side,
                            entry_price=pos["entry"],
                            exit_price=exit_price,
                            quantity=pos["qty"],
                            pnl=net_pnl,
                            fees=pos["fees"] + fees_exit,
                            entry_time=dfs[sym].iloc[pos["bar_open"]]["timestamp"],
                            exit_time=ts,
                            metadata={"reason": reason},
                        )
                        self.trades.append(trade)
                        equity += net_pnl
                        del self.positions[sym]
                        in_position = False

            self.equity_curve.append(equity)
            self.risk.update_equity(equity)

        return self._compute_result()

    def _compute_result(self) -> BacktestResult:
        if not self.trades:
            return BacktestResult(total_return=0.0, equity_curve=self.equity_curve, trades=self.trades)

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
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

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
            metadata={"initial_equity": self.initial_equity, "final_equity": equity_series.iloc[-1]},
        )
