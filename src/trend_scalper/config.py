from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class TradingConfig(BaseModel):
    mode: str = Field(default="backtest")
    binance_testnet: bool = Field(default=True)
    live_trading: bool = Field(default=False)

    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    timeframes: list[str] = Field(default_factory=lambda: ["1m", "5m"])
    trend_window_minutes: int = Field(default=15)
    min_24h_volume_usd: float = Field(default=20_000_000.0)

    leverage: int = Field(default=1)
    risk_per_trade: float = Field(default=0.0025)
    max_daily_loss: float = Field(default=0.01)
    max_open_positions: int = Field(default=1)

    taker_fee: float = Field(default=0.0005)
    slippage_bps: int = Field(default=2)
    stop_loss_pct: float = Field(default=0.003)
    take_profit_pct: float = Field(default=0.006)


class StrategyConfig(BaseModel):
    ema_fast: int = Field(default=9)
    ema_slow: int = Field(default=21)
    rsi_period: int = Field(default=14)
    rsi_lower: float = Field(default=40.0)
    rsi_upper: float = Field(default=70.0)
    volume_spike_mult: float = Field(default=1.5)


class TrendScannerConfig(BaseModel):
    enabled: bool = Field(default=True)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "change_24h": 0.5,
            "volume_24h": 0.35,
            "volatility": 0.15,
        }
    )
    top_n: int = Field(default=3)
    blacklist: list[str] = Field(default_factory=list)


class LoggingConfig(BaseModel):
    level: str = Field(default="INFO")
    file: str = Field(default="logs/trading_agent.log")


class TelegramConfig(BaseModel):
    enabled: bool = Field(default=False)
    bot_token: str = Field(default="")
    chat_id: str = Field(default="")


class AppConfig(BaseModel):
    trading: TradingConfig
    strategy: StrategyConfig
    trend_scanner: TrendScannerConfig
    logging: LoggingConfig
    telegram: TelegramConfig


def load_config(config_path: str | None = None) -> AppConfig:
    load_dotenv()

    if config_path is None:
        config_path = os.getenv("CONFIG_PATH", "config/settings.yaml")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    if env_mode := os.getenv("TRADING_MODE"):
        raw["trading"]["mode"] = env_mode
    if env_testnet := os.getenv("BINANCE_TESTNET"):
        raw["trading"]["binance_testnet"] = env_testnet.lower() in ("1", "true", "yes")
    if env_live := os.getenv("LIVE_TRADING"):
        raw["trading"]["live_trading"] = env_live.lower() in ("1", "true", "yes")

    return AppConfig(
        trading=TradingConfig(**raw.get("trading", {})),
        strategy=StrategyConfig(**raw.get("strategy", {})),
        trend_scanner=TrendScannerConfig(**raw.get("trend_scanner", {})),
        logging=LoggingConfig(**raw.get("logging", {})),
        telegram=TelegramConfig(**raw.get("telegram", {})),
    )
