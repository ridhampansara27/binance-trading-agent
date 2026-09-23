from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

from .config import load_config, AppConfig
from .exchange import ExchangeClient
from .risk import RiskManager
from .execution import ExecutionEngine
from .trend_scanner import TrendScanner
from .notifications import TelegramNotifier
from .models import Side

logger = structlog.get_logger()


def setup_logging(cfg: AppConfig) -> None:
    log_level = getattr(structlog.levels, cfg.logging.level.upper(), structlog.levels.INFO)
    Path(cfg.logging.file).parent.mkdir(parents=True, exist_ok=True)

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer() if os.getenv("LOG_JSON", "0") == "1" else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


async def run_backtest_mode(app_cfg: AppConfig) -> None:
    from .backtester import Backtester
    from .storage import load_klines_parquet, save_backtest_result

    logger.info("Running in backtest mode")

    data_dir = Path(os.getenv("DATA_DIR", "data/klines"))
    if not data_dir.exists():
        logger.warning("No backtest data directory found", path=str(data_dir))
        return

    data: dict[str, dict] = {}
    for sym in app_cfg.trading.symbols:
        for tf in app_cfg.trading.timeframes:
            safe_sym = sym.lower().replace("usdt", "")
            file_path = data_dir / f"{safe_sym}_{tf}.parquet"
            if file_path.exists():
                key = f"{sym}_{tf}"
                data[key] = load_klines_parquet(file_path)
                logger.info("Loaded backtest data", symbol=sym, timeframe=tf, rows=len(data[key]))

    if not data:
        logger.warning("No backtest data loaded")
        return

    backtester = Backtester(app_cfg, data, initial_equity=10000.0)
    result = backtester.run()

    results_dir = Path(os.getenv("RESULTS_DIR", "results"))
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / f"backtest_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"

    save_backtest_result(result, report_path)
    logger.info("Backtest completed", report=str(report_path), total_return=result.total_return)


async def run_testnet_or_live_mode(app_cfg: AppConfig) -> None:
    logger.info("Running in testnet/live mode", mode=app_cfg.trading.mode)

    exchange = ExchangeClient(app_cfg.trading)
    risk = RiskManager(app_cfg.trading)
    executor = ExecutionEngine(exchange, risk, app_cfg.trading)

    scanner = TrendScanner(app_cfg.trading, app_cfg.trend_scanner)
    notifier = TelegramNotifier(app_cfg.telegram)

    current_symbols = list(app_cfg.trading.symbols)

    async def on_trend_update(trending: list[str]) -> None:
        nonlocal current_symbols
        current_symbols = trending
        logger.info("Trending symbols updated", symbols=current_symbols)
        await notifier.send_message(f"🔥 Trending symbols updated:\n" + ", ".join(f"`{s}`" for s in current_symbols))

    scanner_task = asyncio.create_task(scanner.run_periodic(on_trend_update))

    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    finally:
        scanner_task.cancel()
        await notifier.close()


async def main() -> None:
    config_path = os.getenv("CONFIG_PATH", "config/settings.yaml")
    app_cfg = load_config(config_path)

    setup_logging(app_cfg)
    logger.info("Binance Trading Agent starting", mode=app_cfg.trading.mode)

    mode = app_cfg.trading.mode

    if mode == "backtest":
        await run_backtest_mode(app_cfg)
    elif mode == "testnet":
        await run_testnet_or_live_mode(app_cfg)
    elif mode == "live":
        logger.error("Live mode is disabled in this repository implementation.")
    else:
        logger.error("Unknown trading mode", mode=mode)


if __name__ == "__main__":
    asyncio.run(main())
