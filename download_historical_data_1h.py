#!/usr/bin/env python3
"""
Download 1h historical klines from Binance.
"""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
import pandas as pd


async def fetch_klines(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    start_time: datetime,
    end_time: datetime,
) -> pd.DataFrame:
    """Fetch klines from Binance API."""
    base_url = "https://api.binance.com/api/v3/klines"
    
    all_data = []
    current_start = start_time
    
    while current_start < end_time:
        start_ts = int(current_start.timestamp() * 1000)
        
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ts,
            "limit": 1000,
        }
        
        async with session.get(base_url, params=params) as resp:
            if resp.status != 200:
                print(f"Error: {resp.status}")
                break
            
            data = await resp.json()
            if not data:
                break
            
            all_data.extend(data)
            
            last_time = data[-1][0]
            current_start = datetime.fromtimestamp(last_time / 1000) + timedelta(milliseconds=1)
            
            print(f"  {symbol}_{interval}: fetched {len(all_data)} candles...")
    
    if not all_data:
        return pd.DataFrame()
    
    df = pd.DataFrame(
        all_data,
        columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ]
    )
    
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    
    df = df[(df["timestamp"] >= start_time) & (df["timestamp"] <= end_time)]
    
    return df[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


async def download_pair(symbol, interval, start_date, end_date, output_dir):
    """Download klines for a single symbol/interval."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    
    print(f"\nDownloading {symbol} {interval} ({start.date()} to {end.date()})...")
    
    async with aiohttp.ClientSession() as session:
        df = await fetch_klines(session, symbol, interval, start, end)
    
    if df.empty:
        print(f"  ⚠ No data received")
        return
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    filename = f"{symbol.lower()}_{interval}.parquet"
    filepath = output_path / filename
    
    df.to_parquet(filepath, index=False)
    
    print(f"  ✓ Saved {len(df)} rows to {filepath}")


async def main():
    # 90 days: Dec 2025 - Feb 2026
    START_DATE = "2025-12-01"
    END_DATE = "2026-02-28"
    OUTPUT_DIR = "data/klines"
    
    symbols = ["BTCUSDT", "ETHUSDT"]
    intervals = ["1h"]
    
    tasks = []
    for symbol in symbols:
        for interval in intervals:
            tasks.append(
                download_pair(symbol, interval, START_DATE, END_DATE, OUTPUT_DIR)
            )
    
    await asyncio.gather(*tasks)
    
    print("\n✅ All downloads complete!")


if __name__ == "__main__":
    asyncio.run(main())