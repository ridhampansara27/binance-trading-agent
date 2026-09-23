"""Verify public and signed access to Binance USD-M Futures testnet.

This script never places or cancels an order.
"""
import asyncio
import logging

import structlog

from bot_config_loader import load_bot_config
from execution_manager import ExecutionManager


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
    )

    config = load_bot_config("bot_config.yaml")
    executor = ExecutionManager(config, testnet=True)
    public_ok = await executor.check_public_connectivity()
    account_ok = await executor.check_account_access() if public_ok else False

    print(f"Public testnet connectivity: {'OK' if public_ok else 'FAILED'}")
    print(f"Signed Futures testnet account access: {'OK' if account_ok else 'FAILED'}")

    if not account_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
