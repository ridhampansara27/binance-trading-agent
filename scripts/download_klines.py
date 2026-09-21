#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd


async def download_klines(symbol: str, interval: str, days: int, output: str) -> None:
    base = "https://fapi.binance.com/fapi/v1/klines"
    end = datetime.utcnow()
    start = end - timedelta(days=days)

    all_rows = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        current = start
        while current < end:
            start_ts = int(current.timestamp() * 1000)
            params = {"symbol": symbol, "interval": interval, "startTime": start_ts, "limit": 1000}
            resp = await client.get(base, params=params)
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                break

            for r in rows:
                ts = datetime.utcfromtimestamp(r[0] / 1000)
                if ts < start or ts > end:
                    continue
                all_rows.append(
                    {
                        "timestamp": ts,
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                        "quote_volume": float(r[7]),
                        "trades": int(r[8]),
                    }
                )

            last_ts = datetime.utcfromtimestamp(rows[-1][0] / 1000)
            current = last_ts + timedelta(milliseconds=1)

    if not all_rows:
        print("No data downloaded.")
        return

    df = pd.DataFrame(all_rows)
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    print(f"Saved {len(df)} rows to {out_path}")


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(download_klines(args.symbol, args.interval, args.days, args.output))


if __name__ == "__main__":
    cli()
