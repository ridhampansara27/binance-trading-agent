from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class ResearchConfig:
    symbols: list[str]
    signal_timeframe: str = "5m"
    intrabar_timeframe: str = "1m"
    max_hold_minutes: int = 15
    fee_taker: float = 0.0005
    fee_maker: float = 0.0002
    spread_bps: float = 2
    slippage_bps: float = 2
    funding_rate_per_8h: float = 0.0
    latency_bars: int = 1
    one_position_per_symbol: bool = True
    max_concurrent_positions: int = 2
    cooldown_minutes: int = 15
    max_trades_per_day: int = 6
    max_daily_loss_pct: float = 0.01
    min_notional: dict[str, float] | None = None
    min_qty: dict[str, float] | None = None
    step_size: dict[str, float] | None = None
    correlation_groups: list[list[str]] | None = None
    avoid_same_direction_correlated: bool = True
    entry_zscore: float = 2.0
    exit_zscore: float = 0.25
    stop_loss_pct: float = 0.003
    take_profit_pct: float = 0.004
    initial_equity: float = 10_000.0


def normalize_quantity(symbol: str, qty: float, cfg: ResearchConfig, price: float) -> float:
    step = (cfg.step_size or {}).get(symbol, 0.001)
    min_qty = (cfg.min_qty or {}).get(symbol, 0.001)
    min_notional = (cfg.min_notional or {}).get(symbol, 5.0)
    norm = np.floor(qty / step) * step
    if norm < min_qty:
        return 0.0
    if norm * price < min_notional:
        return 0.0
    return float(norm)


def generate_5m_mean_reversion_signals(df_5m: pd.DataFrame, cfg: ResearchConfig) -> pd.DataFrame:
    data = df_5m.copy()
    data["ret"] = data["close"].pct_change()
    data["mean"] = data["ret"].rolling(20).mean()
    data["std"] = data["ret"].rolling(20).std()
    data["z"] = (data["ret"] - data["mean"]) / data["std"]
    data["signal"] = 0
    data.loc[data["z"] <= -cfg.entry_zscore, "signal"] = 1
    data.loc[data["z"] >= cfg.entry_zscore, "signal"] = -1
    data.loc[data["z"].abs() <= cfg.exit_zscore, "signal"] = 0
    return data


def _correlated(symbol: str, other: str, cfg: ResearchConfig) -> bool:
    if symbol == other:
        return True
    for group in cfg.correlation_groups or []:
        group_set = set(group)
        if symbol in group_set and other in group_set:
            return True
    return False


def run_research_backtest(
    bars_5m_by_symbol: dict[str, pd.DataFrame],
    bars_1m_by_symbol: dict[str, pd.DataFrame],
    cfg: ResearchConfig,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    equity = cfg.initial_equity
    open_positions: dict[str, dict[str, Any]] = {}
    daily_pnl: dict[str, float] = {}
    daily_count: dict[str, int] = {}
    cooldown_until: dict[str, datetime] = {}
    rejection_log: list[dict[str, Any]] = []

    signals = {s: generate_5m_mean_reversion_signals(df, cfg) for s, df in bars_5m_by_symbol.items()}
    timeline = sorted(set().union(*[set(pd.to_datetime(df["timestamp"]).tolist()) for df in signals.values()]))

    for ts in timeline:
        day = str(ts.date())
        if daily_pnl.get(day, 0.0) <= -(cfg.max_daily_loss_pct * cfg.initial_equity):
            continue

        for symbol, pos in list(open_positions.items()):
            intrabar = bars_1m_by_symbol[symbol]
            window = intrabar[(intrabar["timestamp"] >= ts) & (intrabar["timestamp"] < ts + timedelta(minutes=5))]
            if window.empty:
                px = float(bars_5m_by_symbol[symbol].loc[bars_5m_by_symbol[symbol]["timestamp"] == ts, "close"].iloc[0])
                high = low = px
            else:
                high = float(window["high"].max())
                low = float(window["low"].min())
                px = float(window["close"].iloc[-1])

            exit_reason = None
            if pos["side"] == 1:
                if low <= pos["stop"]:
                    px = pos["stop"]
                    exit_reason = "stop_loss"
                elif high >= pos["take"]:
                    px = pos["take"]
                    exit_reason = "take_profit"
            else:
                if high >= pos["stop"]:
                    px = pos["stop"]
                    exit_reason = "stop_loss"
                elif low <= pos["take"]:
                    px = pos["take"]
                    exit_reason = "take_profit"

            if exit_reason is None and ts >= pos["max_exit_ts"]:
                exit_reason = "max_hold"
            if exit_reason is None:
                continue

            gross = (px - pos["entry_price"]) * pos["qty"] * pos["side"]
            notional = px * pos["qty"]
            fee = notional * cfg.fee_taker
            funding = notional * cfg.funding_rate_per_8h * (cfg.max_hold_minutes / (8 * 60))
            pnl = gross - fee - pos["entry_fee"] - funding
            equity += pnl
            daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl
            cooldown_until[symbol] = ts + timedelta(minutes=cfg.cooldown_minutes)
            trades.append(
                {
                    "symbol": symbol,
                    "entry_time": pos["entry_time"],
                    "exit_time": ts,
                    "side": "LONG" if pos["side"] == 1 else "SHORT",
                    "entry_price": pos["entry_price"],
                    "exit_price": px,
                    "qty": pos["qty"],
                    "pnl": pnl,
                    "exit_reason": exit_reason,
                }
            )
            del open_positions[symbol]

        for symbol in cfg.symbols:
            if symbol in open_positions and cfg.one_position_per_symbol:
                continue
            if len(open_positions) >= cfg.max_concurrent_positions:
                continue
            if daily_count.get(day, 0) >= cfg.max_trades_per_day:
                continue
            if ts < cooldown_until.get(symbol, datetime.min.replace(tzinfo=ts.tzinfo)):
                continue

            row = signals[symbol][signals[symbol]["timestamp"] == ts]
            if row.empty:
                continue
            signal = int(row["signal"].iloc[0])
            if signal == 0:
                continue

            if cfg.avoid_same_direction_correlated:
                rejected = False
                for other_symbol, pos in open_positions.items():
                    if pos["side"] == signal and _correlated(symbol, other_symbol, cfg):
                        rejection_log.append(
                            {
                                "timestamp": ts.isoformat(),
                                "symbol": symbol,
                                "reason": f"correlation-guard:{other_symbol}",
                            }
                        )
                        rejected = True
                        break
                if rejected:
                    continue

            next_index = signals[symbol].index[signals[symbol]["timestamp"] == ts]
            if len(next_index) == 0:
                continue
            entry_bar_idx = int(next_index[0]) + cfg.latency_bars
            if entry_bar_idx >= len(signals[symbol]):
                continue

            entry_bar = signals[symbol].iloc[entry_bar_idx]
            entry_price = float(entry_bar["open"])
            spread = entry_price * (cfg.spread_bps / 10000)
            slip = entry_price * (cfg.slippage_bps / 10000)
            if signal == 1:
                entry_price += spread + slip
            else:
                entry_price -= spread + slip
            qty = normalize_quantity(symbol, (equity * 0.001) / max(entry_price, 1), cfg, entry_price)
            if qty <= 0:
                rejection_log.append({"timestamp": ts.isoformat(), "symbol": symbol, "reason": "min-filter-reject"})
                continue

            stop = entry_price * (1 - cfg.stop_loss_pct) if signal == 1 else entry_price * (1 + cfg.stop_loss_pct)
            take = entry_price * (1 + cfg.take_profit_pct) if signal == 1 else entry_price * (1 - cfg.take_profit_pct)
            open_positions[symbol] = {
                "side": signal,
                "qty": qty,
                "entry_price": entry_price,
                "entry_fee": entry_price * qty * cfg.fee_taker,
                "entry_time": entry_bar["timestamp"],
                "max_exit_ts": entry_bar["timestamp"] + timedelta(minutes=cfg.max_hold_minutes),
                "stop": stop,
                "take": take,
            }
            daily_count[day] = daily_count.get(day, 0) + 1

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {"trades": [], "summary": {"net_return": 0.0, "trade_count": 0}, "rejections": rejection_log}

    wins = trades_df[trades_df["pnl"] > 0]["pnl"]
    losses = trades_df[trades_df["pnl"] <= 0]["pnl"]
    net_return = float((trades_df["pnl"].sum()) / cfg.initial_equity)
    expectancy = float(trades_df["pnl"].mean())
    profit_factor = float(wins.sum() / abs(losses.sum())) if not losses.empty else float("inf")
    pnl_by_symbol = trades_df.groupby("symbol")["pnl"].sum().to_dict()
    exit_reason = trades_df.groupby("exit_reason").size().to_dict()
    exposure = float(len(trades_df) * cfg.max_hold_minutes / max(len(timeline) * 5, 1))

    returns = trades_df["pnl"] / cfg.initial_equity
    sharpe = float((returns.mean() / returns.std()) * np.sqrt(252)) if returns.std() > 0 else 0.0
    downside = returns[returns < 0]
    sortino = float((returns.mean() / downside.std()) * np.sqrt(252)) if downside.std() > 0 else 0.0

    return {
        "trades": trades_df.to_dict(orient="records"),
        "rejections": rejection_log,
        "summary": {
            "net_return": net_return,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
            "drawdown": float(min(0.0, returns.cumsum().min())),
            "sharpe": sharpe,
            "sortino": sortino,
            "trade_count": len(trades_df),
            "win_loss_sizes": {
                "avg_win": float(wins.mean()) if not wins.empty else 0.0,
                "avg_loss": float(losses.mean()) if not losses.empty else 0.0,
            },
            "pnl_by_symbol": pnl_by_symbol,
            "exit_reason": exit_reason,
            "exposure": exposure,
        },
    }
