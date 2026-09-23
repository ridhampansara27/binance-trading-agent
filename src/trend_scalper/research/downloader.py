from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

DEMO_BASE = "https://demo-fapi.binance.com"


def download_futures_klines(symbol: str, interval: str, days: int) -> pd.DataFrame:
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=days)
    all_rows: list[list] = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1500,
        }
        response = requests.get(f"{DEMO_BASE}/fapi/v1/klines", params=params, timeout=20)
        response.raise_for_status()
        rows = response.json()
        if not rows:
            break
        all_rows.extend(rows)
        cursor = int(rows[-1][6]) + 1

    if not all_rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time", "quote_asset_volume",
        "number_of_trades", "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def save_research_data(df: pd.DataFrame, symbol: str, interval: str, output_dir: str = "data/research") -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / f"{symbol.lower()}_{interval}.parquet"
    df.to_parquet(out, index=False)
    return out
