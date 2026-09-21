from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

from .config import TrendScannerConfig, TradingConfig


@dataclass
class Ticker24h:
    symbol: str
    price_change_percent: float
    quote_volume_24h: float
    last_price: float
    high_24h: float
    low_24h: float
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


class TrendScanner:
    def __init__(
        self,
        trading_cfg: TradingConfig,
        scanner_cfg: TrendScannerConfig,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.trading_cfg = trading_cfg
        self.scanner_cfg = scanner_cfg
        self.client = client or httpx.AsyncClient(timeout=10.0)

        self.weights = scanner_cfg.weights
        self.top_n = scanner_cfg.top_n
        self.blacklist = set(scanner_cfg.blacklist)
        self.min_volume = trading_cfg.min_24h_volume_usd

    async def fetch_tickers(self) -> list[Ticker24h]:
        base_url = "https://fapi.binance.com"
        endpoint = f"{base_url}/fapi/v1/ticker/24hr"

        resp = await self.client.get(endpoint)
        resp.raise_for_status()
        data = resp.json()

        tickers: list[Ticker24h] = []
        for row in data:
            symbol = row["symbol"]
            if not symbol.endswith("USDT"):
                continue
            if symbol in self.blacklist:
                continue

            quote_vol = float(row.get("quoteVolume", 0) or 0)
            if quote_vol < self.min_volume:
                continue

            pct_change = float(row.get("priceChangePercent", 0) or 0)
            last_price = float(row.get("lastPrice", 0) or 0)
            high = float(row.get("highPrice", 0) or 0)
            low = float(row.get("lowPrice", 0) or 0)

            tickers.append(
                Ticker24h(
                    symbol=symbol,
                    price_change_percent=pct_change,
                    quote_volume_24h=quote_vol,
                    last_price=last_price,
                    high_24h=high,
                    low_24h=low,
                )
            )

        return tickers

    def compute_trend_score(self, t: Ticker24h, all_tickers: list[Ticker24h]) -> float:
        if not all_tickers:
            return 0.0

        max_change = max(abs(x.price_change_percent) for x in all_tickers) or 1.0
        max_vol = max(x.quote_volume_24h for x in all_tickers) or 1.0

        vol_range = max(x.high_24h - x.low_24h for x in all_tickers) or 1.0
        volatility = (t.high_24h - t.low_24h) / vol_range

        norm_change = abs(t.price_change_percent) / max_change
        norm_vol = t.quote_volume_24h / max_vol

        score = (
            self.weights.get("change_24h", 0.5) * norm_change
            + self.weights.get("volume_24h", 0.35) * norm_vol
            + self.weights.get("volatility", 0.15) * volatility
        )
        return score

    async def get_trending_symbols(self) -> list[str]:
        tickers = await self.fetch_tickers()
        if not tickers:
            return self.trading_cfg.symbols[: self.top_n]

        scored = [(t, self.compute_trend_score(t, tickers)) for t in tickers]
        scored.sort(key=lambda x: x[1], reverse=True)

        top = [t.symbol for t, _ in scored[: self.top_n]]
        return top

    async def run_periodic(self, callback) -> None:
        interval_sec = self.trading_cfg.trend_window_minutes * 60
        while True:
            try:
                trending = await self.get_trending_symbols()
                await callback(trending)
            except Exception:
                pass
            await asyncio.sleep(interval_sec)
