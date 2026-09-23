from __future__ import annotations

import argparse
import asyncio

from trend_scalper.config import load_config
from trend_scalper.main import run_testnet_or_live_mode, setup_logging


async def run_testnet(config_path: str, mode: str) -> None:
    if mode != "testnet":
        raise RuntimeError("This repository implementation is Demo/Testnet-only.")
    app_cfg = load_config(config_path)
    app_cfg.trading.mode = mode
    setup_logging(app_cfg)

    await run_testnet_or_live_mode(app_cfg)


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--mode", default="testnet", choices=["testnet"])
    args = parser.parse_args()
    asyncio.run(run_testnet(args.config, args.mode))


if __name__ == "__main__":
    cli()
