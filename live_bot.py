"""Testnet-only Binance Futures demo trading bot."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pandas as pd
import structlog

from bot_config_loader import load_bot_config
from execution_manager import ExecutionManager
from journal import TradeJournal
from mean_reversion_strategy_live import MeanReversionStrategy
from risk_manager import RiskManager
from trend_scalper.models import Signal
from trend_strategy_live import TrendFollowingStrategy

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
            self.candles[key] = self.candles[key].iloc[-self.max_candles :].reset_index(drop=True)

    def get_df(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        return self.candles.get(f"{symbol}_{timeframe}")


class SignalGenerator:
    def __init__(self, config: dict, cache: DataCache):
        self.cache = cache
        self.mean_reversion = MeanReversionStrategy(config)
        self.trend_following = TrendFollowingStrategy(config)

    def check_signals(self, symbol: str, timeframe: str, current_price: float) -> Signal | None:
        df = self.cache.get_df(symbol, timeframe)
        if df is None or len(df) < 50:
            return None

        signal = self.mean_reversion.generate_signal(df, symbol, current_price)
        if signal:
            return signal

        signal = self.trend_following.generate_signal(df, symbol, current_price)
        return signal


class TradingBot:
    def __init__(self, config_path: str = "bot_config.yaml"):
        self.config = load_bot_config(config_path)
        self.journal = TradeJournal()

        trading = self.config.get("trading", {})
        self.max_positions = int(trading.get("max_positions", 2))
        self.symbols = list(self.config.get("symbols", ["BTCUSDT", "ETHUSDT", "SOLUSDT"]))
        self.timeframe = str(self.config.get("timeframes", ["1h"])[0])

        self.cache = DataCache()
        self.signal_generator = SignalGenerator(self.config, self.cache)
        self.risk_manager = RiskManager(self.config)
        self.executor = ExecutionManager(self.config, testnet=True, journal=self.journal)
        self.positions: dict[str, dict] = {}
        self.running = False
        self.last_heartbeat: dict[str, int] = {}

    async def reconcile(self) -> dict:
        state = await self.executor.reconcile_account_state()
        exchange_positions = state["positions"]
        open_orders = state["open_orders"]
        open_algo_orders = state["open_algo_orders"]

        for position in exchange_positions:
            symbol = position.get("symbol")
            if not symbol:
                continue
            qty = abs(float(position.get("positionAmt", "0")))
            side = "BUY" if float(position.get("positionAmt", "0")) > 0 else "SELL"
            protected = any(o.get("symbol") == symbol for o in open_algo_orders)
            self.positions[symbol] = {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "entry_price": float(position.get("entryPrice", "0")),
                "protected": protected,
                "unmanaged": True,
            }
            self.journal.log_position_event(
                symbol,
                "startup_reconcile_adopted",
                {"qty": qty, "side": side, "protected": protected},
            )
            if not protected:
                self.journal.log_incident(
                    severity="critical",
                    code="unprotected-reconciled-position",
                    message=f"Reconciled position {symbol} has no protective algo orders.",
                    payload={"symbol": symbol, "qty": qty},
                )

        return {
            "exchange_positions": len(exchange_positions),
            "open_orders": len(open_orders),
            "open_algo_orders": len(open_algo_orders),
            "adopted_positions": len(self.positions),
        }

    async def status(self) -> dict:
        summary = await self.reconcile()
        return {
            "symbols": self.symbols,
            "timeframe": self.timeframe,
            "positions": self.positions,
            "risk": {
                "daily_trade_count": self.risk_manager.daily_trade_count,
                "daily_pnl_pct": self.risk_manager.daily_pnl_pct,
            },
            "exchange": summary,
        }

    async def start(self) -> None:
        log.info(
            "Starting demo-trading bot",
            testnet=True,
            symbols=self.symbols,
            timeframe=self.timeframe,
            max_positions=self.max_positions,
        )

        if not await self.executor.check_public_connectivity():
            raise RuntimeError("Cannot reach Binance Futures Demo endpoint.")
        if not await self.executor.check_account_access():
            raise RuntimeError("Cannot access Binance Futures Demo account.")

        mark_prices = await self.executor.get_mark_prices(self.symbols)
        await self.executor.validate_symbol_quantities(self.symbols, mark_prices)
        reconcile_summary = await self.reconcile()
        log.info("Startup reconciliation completed", **reconcile_summary)

        await self._load_historical_data()
        await self._consume_websocket_forever()

    async def _load_historical_data(self) -> None:
        from trend_scalper.storage import load_klines_parquet

        for symbol in self.symbols:
            path = Path("data") / "klines" / f"{symbol.lower()}_{self.timeframe}.parquet"
            if not path.exists():
                continue

            df = load_klines_parquet(path).tail(500).copy()
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            self.cache.candles[f"{symbol}_{self.timeframe}"] = df.reset_index(drop=True)
            log.info("Historical candles loaded", symbol=symbol, candles=len(df))

    async def _consume_websocket_forever(self) -> None:
        streams = "/".join(f"{symbol.lower()}@kline_{self.timeframe}" for symbol in self.symbols)
        websocket_url = f"wss://demo-fstream.binance.com/stream?streams={streams}"
        log.info("Connecting Demo Futures websocket", url=websocket_url)

        retry_seconds = 5
        while True:
            try:
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=None)
                async with aiohttp.ClientSession(timeout=timeout) as session, session.ws_connect(
                    websocket_url, heartbeat=30
                ) as websocket:
                    self.running = True
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
            except (
                aiohttp.ClientError,
                json.JSONDecodeError,
                RuntimeError,
                ValueError,
                KeyError,
            ) as error:
                self.running = False
                self.journal.log_incident(
                    severity="error",
                    code="ws-disconnect",
                    message="Websocket disconnected; retrying.",
                    payload={"error": str(error), "delay_seconds": retry_seconds},
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

        if not kline.get("x", False):
            event_time_ms = int(data.get("E", 0))
            event_minute = event_time_ms // 60_000
            if self.last_heartbeat.get(symbol) != event_minute and event_minute % 5 == 0:
                self.last_heartbeat[symbol] = event_minute
                log.info("Heartbeat", symbol=symbol, active_price=float(kline["c"]))
            return

        price = float(kline["c"])
        if symbol in self.positions:
            await self._check_local_exit(symbol, price)
            return

        signal = self.signal_generator.check_signals(symbol, self.timeframe, price)
        if signal is None:
            return

        self.journal.log_signal(symbol, signal.side.value, {"entry_price": signal.entry_price})
        can_open, reason = self.risk_manager.can_open_position(signal, self.positions)
        if not can_open:
            log.info("Signal rejected", symbol=symbol, reason=reason)
            self.journal.log_incident(
                severity="warning",
                code="risk-rejection",
                message=f"Signal rejected by risk manager: {reason}",
                payload={"symbol": symbol, "reason": reason},
            )
            return

        try:
            order = await self.executor.open_position(signal)
            if order is None:
                return
            self.positions[symbol] = order
            self.risk_manager.record_entry()
            self.journal.log_position_event(symbol, "opened", order)
        except (RuntimeError, ValueError, KeyError) as error:
            self.journal.log_incident(
                severity="error",
                code="entry-failed",
                message="Entry failed.",
                payload={"symbol": symbol, "error": str(error)},
            )

    async def _check_local_exit(self, symbol: str, price: float) -> None:
        position = self.positions[symbol]
        side = position.get("side")
        stop_loss = float(position.get("stop_loss", 0))
        take_profit = float(position.get("take_profit", 0))
        breached = (side == "BUY" and (price <= stop_loss or price >= take_profit)) or (
            side == "SELL" and (price >= stop_loss or price <= take_profit)
        )
        if breached:
            log.info("Exchange-side protection expected to close position", symbol=symbol, price=price)

    async def stop(self) -> None:
        self.running = False
        for symbol, position in self.positions.items():
            self.journal.log_position_event(
                symbol,
                "shutdown-open-position",
                {
                    "qty": position.get("qty"),
                    "protected": position.get("protected", False),
                    "stop_algo_id": position.get("stop_algo_id"),
                    "take_profit_algo_id": position.get("take_profit_algo_id"),
                },
            )
        log.warning("Bot stopped. No duplicate close orders submitted on shutdown.")


def _setup_logging() -> None:
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


async def _main_async(args: argparse.Namespace) -> None:
    bot = TradingBot(args.config)
    if args.command == "status":
        status = await bot.status()
        print(json.dumps(status, indent=2, default=str))
        return
    if args.command == "reconcile":
        result = await bot.reconcile()
        print(json.dumps(result, indent=2))
        return

    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.stop()
    except Exception as error:
        log.exception("Fatal bot error", error=str(error))
        await bot.stop()


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(description="Demo-only 1h trading bot")
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "status", "reconcile"],
    )
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
