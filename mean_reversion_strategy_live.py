"""Mean-reversion signal generator for the 1-hour paper-trading bot."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import ta.momentum as ta_momentum
import ta.volatility as ta_volatility

from trend_scalper.models import Side, Signal


class MeanReversionStrategy:
    def __init__(self, config: dict):
        strategy = config.get("strategy", {})
        self.rsi_period = int(strategy.get("rsi_period", 14))
        self.rsi_oversold = float(strategy.get("rsi_oversold", 30))
        self.rsi_overbought = float(strategy.get("rsi_overbought", 70))
        self.bb_window = int(strategy.get("bb_window", 20))
        self.bb_std = float(strategy.get("bb_std", 2.0))
        self.stop_loss_pct = float(strategy.get("mean_reversion_stop_loss_pct", 0.02))

    def generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        current_price: float,
    ) -> Optional[Signal]:
        required_rows = max(self.rsi_period, self.bb_window) + 2
        if df is None or len(df) < required_rows:
            return None

        data = df.copy()
        data["rsi"] = ta_momentum.rsi(data["close"], window=self.rsi_period)
        bands = ta_volatility.BollingerBands(
            close=data["close"], window=self.bb_window, window_dev=self.bb_std
        )
        data["bb_upper"] = bands.bollinger_hband()
        data["bb_middle"] = bands.bollinger_mavg()
        data["bb_lower"] = bands.bollinger_lband()

        row = data.iloc[-1]
        if pd.isna(row["rsi"]) or pd.isna(row["bb_middle"]):
            return None

        if row["rsi"] < self.rsi_oversold and current_price < row["bb_lower"]:
            side = Side.LONG
            stop_loss = current_price * (1 - self.stop_loss_pct)
        elif row["rsi"] > self.rsi_overbought and current_price > row["bb_upper"]:
            side = Side.SHORT
            stop_loss = current_price * (1 + self.stop_loss_pct)
        else:
            return None

        take_profit = float(row["bb_middle"])
        if (side == Side.LONG and take_profit <= current_price) or (
            side == Side.SHORT and take_profit >= current_price
        ):
            return None

        return Signal(
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            side=side,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strength=1.0,
            metadata={
                "strategy": "mean_reversion",
                "rsi": float(row["rsi"]),
                "bb_lower": float(row["bb_lower"]),
                "bb_middle": take_profit,
                "bb_upper": float(row["bb_upper"]),
            },
        )
