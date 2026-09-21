from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def load_klines_parquet(path: str | Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "timestamp" in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def save_klines_parquet(df: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def save_backtest_result(result: Any, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    trades_data = []
    for t in result.trades:
        trades_data.append(
            {
                "symbol": t.symbol,
                "side": t.side.value,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "quantity": t.quantity,
                "pnl": t.pnl,
                "fees": t.fees,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "metadata": t.metadata,
            }
        )

    report = {
        "total_return": result.total_return,
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "win_rate": result.win_rate,
        "avg_win": result.avg_win,
        "avg_loss": result.avg_loss,
        "profit_factor": result.profit_factor,
        "max_drawdown": result.max_drawdown,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "expectancy": result.expectancy,
        "initial_equity": result.metadata.get("initial_equity"),
        "final_equity": result.metadata.get("final_equity"),
        "trades": trades_data,
        "equity_curve": result.equity_curve,
    }

    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
