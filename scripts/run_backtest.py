#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import structlog
from structlog import stdlib

from trend_scalper.config import load_config
from trend_scalper.backtester import Backtester
from trend_scalper.storage import load_klines_parquet, save_backtest_result


def setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


async def run_backtest(config_path: str, data_dir: str, output: str) -> None:
    setup_logging()
    log = structlog.get_logger()

    app_cfg = load_config(config_path)
    app_cfg.trading.mode = "backtest"

    data: dict[str, dict] = {}
    data_path = Path(data_dir)
    if not data_path.exists():
        log.warning("Data directory not found", path=str(data_path))
        return

    for sym in app_cfg.trading.symbols:
        for tf in app_cfg.trading.timeframes:
            safe_sym = sym.lower()
            file_path = data_path / f"{safe_sym}_{tf}.parquet"
            if file_path.exists():
                key = f"{sym}_{tf}"
                data[key] = load_klines_parquet(file_path)
                log.info("Loaded backtest data", symbol=sym, timeframe=tf, rows=len(data[key]))

    if not data:
        log.warning("No backtest data loaded")
        return

    backtester = Backtester(app_cfg, data, initial_equity=10000.0)
    result = backtester.run()

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_backtest_result(result, out_path)
    log.info("Backtest completed", report=str(out_path), total_return=result.total_return)


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--data-dir", default="data/klines")
    parser.add_argument("--output", default="results/backtest_report.json")
    args = parser.parse_args()
    asyncio.run(run_backtest(args.config, args.data_dir, args.output))


if __name__ == "__main__":
    cli()
