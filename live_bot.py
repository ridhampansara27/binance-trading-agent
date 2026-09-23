#!/usr/bin/env python3
"""Testnet-only Binance Futures demo trading bot."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import pandas as pd
import structlog
import yaml

from execution_manager import ExecutionManager
from mean_reversion_strategy_live import MeanReversionStrategy
from risk_manager import RiskManager
from trend_strategy_live import TrendFollowingStrategy
from trend_scalper.models import Side, Signal

log = structlog.get_logger()


class DataCache:
    def __init__(self, max_candles: int = 500):
        self.max_candles = max_candles
        self.candles: dict[str, pd.DataFrame] = {}

    def add_candle(self, symbol: str, timeframe: str, candle: dict) -> None:
        key = f"{symbol}_{timeframe}"
        row = {
            "timestamp": datetime.fromtimestamp(candle["t"] / 1000, tz=timezone.utc),
            "open": float(candle["o"]),
            "high": float(candle["h"]),
            "low": float(candle["l"]),
            "close": float(candle["c"]),
            "volume": float(candle["v"]),
        }

        if key not in self.candles:
            self.candles[key] = pd.DataFrame([row])
            return

        df = self.candles[key]
        if df.iloc[-1]["timestamp"] == row["timestamp"]:
            for column, value in row.items():
                df.loc[df.index[-1], column] = value
        else:
            self.candles[key] = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

        if len(self.candles[key]) > self.max_candles:
            self.candles[key] = self.candles[key].iloc[-self.max_candles:].reset_index(drop=True)

    def get_df(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        return self.candles.get(f"{symbol}_{timeframe}")


class SignalGenerator:
    def __init__(self, config: dict, cache: DataCache):
        self.cache = cache
        self.mean_reversion = MeanReversionStrategy(config)
        self.trend_following = TrendFollowingStrategy(config)

    def check_signals(self, symbol: str, timeframe: str, current_price: float) -> Optional[Signal]:
        df = self.cache.get_df(symbol, timeframe)
        if df is None or len(df) < 50:
            return None

        signal = self.mean_reversion.generate_signal(df, symbol, current_price)
        if signal:
            log.info(
                "Mean-reversion signal",
                symbol=symbol,
                side=signal.side.value,
                price=current_price,
            )
            return signal

        signal = self.trend_following.generate_signal(df, symbol, current_price)
        if signal:
            log.info(
                "Trend-following signal",
                symbol=symbol,
                side=signal.side.value,
                price=current_price,
            )
            return signal

        return None


class TradingBot:
    def __init__(self, config_path: str = "bot_config.yaml"):
        with open(config_path, encoding="utf-8") as file:
            self.config = yaml.safe_load(file) or {}

        binance = self.config.get("binance", {})
        self.testnet = bool(binance.get("testnet", True))
        if not self.testnet:
            raise RuntimeError("This build is testnet-only; set binance.testnet: true.")

        trading = self.config.get("trading", {})
        self.max_positions = int(trading.get("max_positions", 1))
        self.symbols = list(self.config.get("symbols", ["BTCUSDT", "ETHUSDT"]))
        self.timeframe = str(self.config.get("timeframes", ["1h"])[0])

        self.cache = DataCache()
        self.signal_generator = SignalGenerator(self.config, self.cache)
        self.risk_manager = RiskManager(self.config)
        self.executor = ExecutionManager(self.config, testnet=True)
        self.positions: dict[str, dict] = {}
        self.running = False

        # Required by the rate-limited heartbeat in _handle_kline.
        self.last_heartbeat: dict[str, int] = {}

    async def start(self) -> None:
        log.info(
            "Starting paper-trading bot",
            testnet=True,
            symbols=self.symbols,
            timeframe=self.timeframe,
            max_positions=self.max_positions,
        )

        if not await self.executor.check_public_connectivity():
            raise RuntimeError("Cannot reach Binance Futures Demo Trading endpoint.")
        if not await self.executor.check_account_access():
            raise RuntimeError("Cannot access the Binance Futures Demo account.")

        await self._load_historical_data()
        await self._consume_websocket_forever()

    async def _load_historical_data(self) -> None:
        log.info("Loading local historical candles")
        from trend_scalper.storage import load_klines_parquet

        for symbol in self.symbols:
            path = Path("data") / "klines" / f"{symbol.lower()}_{self.timeframe}.parquet"
            if not path.exists():
                log.warning("Historical file not found", symbol=symbol, path=str(path))
                continue

            df = load_klines_parquet(path).tail(500).copy()
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            self.cache.candles[f"{symbol}_{self.timeframe}"] = df.reset_index(drop=True)
            log.info("Historical candles loaded", symbol=symbol, candles=len(df))

    async def _consume_websocket_forever(self) -> None:
        streams = "/".join(f"{symbol.lower()}@kline_{self.timeframe}" for symbol in self.symbols)
        websocket_url = f"wss://demo-fstream.binance.com/stream?streams={streams}"
        log.info("Connecting to Binance Futures testnet WebSocket", url=websocket_url)

        retry_seconds = 5
        while True:
            try:
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=None)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(websocket_url, heartbeat=30) as websocket:
                        self.running = True
                        log.info("WebSocket connected")
                        retry_seconds = 5

                        async for message in websocket:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                envelope = json.loads(message.data)
                                payload = envelope.get("data", envelope)
                                await self._handle_kline(payload)
                            elif message.type == aiohttp.WSMsgType.ERROR:
                                raise RuntimeError(f"WebSocket error: {websocket.exception()}")
                            elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING}:
                                break
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.running = False
                log.exception(
                    "WebSocket disconnected; retrying",
                    error=str(error),
                    delay_seconds=retry_seconds,
                )
                await asyncio.sleep(retry_seconds)
                retry_seconds = min(retry_seconds * 2, 60)

    async def _handle_kline(self, data: dict) -> None:
        if "k" not in data or "s" not in data:
            return

        kline = data["k"]
        symbol = data["s"]
        if symbol not in self.symbols:
            return

        self.cache.add_candle(symbol, self.timeframe, kline)

        # Low-noise liveness log: once per symbol every five minutes.
        if not kline.get("x", False):
            event_time_ms = int(data.get("E", 0))
            event_minute = event_time_ms // 60_000
            if (
                self.last_heartbeat.get(symbol) != event_minute
                and event_minute % 5 == 0
            ):
                self.last_heartbeat[symbol] = event_minute
                log.info(
                    "WebSocket heartbeat",
                    symbol=symbol,
                    active_price=float(kline["c"]),
                    candle_closes_at=datetime.fromtimestamp(
                        kline["T"] / 1000,
                        tz=timezone.utc,
                    ).isoformat(),
                )
            return

        price = float(kline["c"])
        log.info("Hourly candle closed", symbol=symbol, close=price)

        if symbol in self.positions:
            await self._check_local_exit(symbol, price)
            return

        if len(self.positions) >= self.max_positions:
            log.info("Signal evaluation skipped: max positions reached", symbol=symbol)
            return

        await self._check_entry(symbol, price)

    async def _check_entry(self, symbol: str, price: float) -> None:
        signal = self.signal_generator.check_signals(symbol, self.timeframe, price)
        if signal is None:
            return

        if not self.risk_manager.can_open_position(signal):
            log.warning("Signal rejected by risk manager", symbol=symbol)
            return

        try:
            order = await self.executor.open_position(signal)
        except Exception as error:
            log.exception("Entry failed", symbol=symbol, error=str(error))
            return

        if order is None:
            log.error("Entry returned no order", symbol=symbol)
            return

        self.positions[symbol] = {
            "signal": signal,
            "order": order,
            "entry_time": datetime.now(timezone.utc),
        }
        log.info(
            "Local position recorded",
            symbol=symbol,
            side=signal.side.value,
            entry_order_id=order["order_id"],
            stop_algo_id=order.get("stop_algo_id"),
            take_profit_algo_id=order.get("take_profit_algo_id"),
        )

    async def _check_local_exit(self, symbol: str, price: float) -> None:
        position = self.positions[symbol]
        signal: Signal = position["signal"]
        breached = (
            signal.side == Side.LONG
            and (price <= signal.stop_loss or price >= signal.take_profit)
        ) or (
            signal.side == Side.SHORT
            and (price >= signal.stop_loss or price <= signal.take_profit)
        )
        if breached:
            log.warning(
                "Local exit threshold observed; exchange protection should manage it",
                symbol=symbol,
                price=price,
            )

    async def stop(self) -> None:
        self.running = False
        log.warning(
            "Bot stopped. Verify Demo Trading positions and protective orders."
        )


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    bot = TradingBot("bot_config.yaml")
    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.stop()
    except Exception as error:
        log.exception("Fatal bot error", error=str(error))
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
