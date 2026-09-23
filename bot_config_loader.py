"""Load local demo-bot config with environment variable credential override."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_bot_config(config_path: str = "bot_config.yaml") -> dict[str, Any]:
    load_dotenv()

    config = _read_yaml(Path(config_path))
    if not config:
        config = _read_yaml(Path("bot_config.example.yaml"))

    binance = dict(config.get("binance", {}))
    env_key = os.getenv("BINANCE_API_KEY")
    env_secret = os.getenv("BINANCE_API_SECRET")
    if env_key:
        binance["api_key"] = env_key
    if env_secret:
        binance["api_secret"] = env_secret
    config["binance"] = binance

    trading = dict(config.get("trading", {}))
    config["trading"] = trading
    config.setdefault("symbols", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    config.setdefault("timeframes", ["1h"])

    if not bool(binance.get("testnet", True)):
        raise RuntimeError("This implementation is Demo/Testnet-only. Set binance.testnet: true.")

    return config
