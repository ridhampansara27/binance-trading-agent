"""Trend-following signal generator for the 1-hour paper-trading bot."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import ta.trend as ta_trend

from trend_scalper.models import Side, Signal


class TrendFollowingStrategy:
    def __init__(self, config: dict):
        strategy = config.get("strategy", {})
        self.fast_ema = int(strategy.get("trend_fast_ema", 12))
        self.slow_ema = int(strategy.get("trend_slow_ema", 26))
        self.adx_window = int(strategy.get("adx_window", 14))
        self.adx_threshold = float(strategy.get("trend_adx_threshold", 25))
        self.stop_loss_pct = float(strategy.get("trend_stop_loss_pct", 0.03))
        self.take_profit_pct = float(strategy.get("trend_take_profit_pct", 0.09))

    def generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        current_price: float,
    ) -> Optional[Signal]:
        required_rows = max(self.slow_ema + 2, self.adx_window * 3)
        if df is None or len(df) < required_rows:
            return None

        data = df.copy()
        data["ema_fast"] = ta_trend.ema_indicator(data["close"], window=self.fast_ema)
        data["ema_slow"] = ta_trend.ema_indicator(data["close"], window=self.slow_ema)
        adx = ta_trend.ADXIndicator(
            high=data["high"], low=data["low"], close=data["close"], window=self.adx_window
        )
        data["adx"] = adx.adx()

        current = data.iloc[-1]
        previous = data.iloc[-2]
        if pd.isna(current["ema_fast"]) or pd.isna(current["ema_slow"]):
            return None
        if current["adx"] < self.adx_threshold:
            return None

        if previous["ema_fast"] <= previous["ema_slow"] and current["ema_fast"] > current["ema_slow"]:
            side = Side.LONG
            stop_loss = current_price * (1 - self.stop_loss_pct)
            take_profit = current_price * (1 + self.take_profit_pct)
        elif previous["ema_fast"] >= previous["ema_slow"] and current["ema_fast"] < current["ema_slow"]:
            side = Side.SHORT
            stop_loss = current_price * (1 + self.stop_loss_pct)
            take_profit = current_price * (1 - self.take_profit_pct)
        else:
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
                "strategy": "trend_following",
                "adx": float(current["adx"]),
                "ema_fast": float(current["ema_fast"]),
                "ema_slow": float(current["ema_slow"]),
            },
        )
