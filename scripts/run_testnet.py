#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os

import structlog

from trend_scalper.config import load_config
from trend_scalper.main import run_testnet_or_live_mode, setup_logging


async def run_testnet(config_path: str, mode: str) -> None:
    app_cfg = load_config(config_path)
    app_cfg.trading.mode = mode

    if mode == "live":
        structlog.configure(
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(structlog.levels.INFO),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
        structlog.get_logger().warning("LIVE TRADING MODE - REAL MONEY AT RISK")
    else:
        setup_logging(app_cfg)

    await run_testnet_or_live_mode(app_cfg)


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--mode", default="testnet", choices=["testnet", "live"])
    args = parser.parse_args()
    asyncio.run(run_testnet(args.config, args.mode))


if __name__ == "__main__":
    cli()
