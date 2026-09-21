from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from .models import Signal, Side
from .config import StrategyConfig


class EMARSIStrategy:
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg

    def generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        current_price: float,
        volume_spike_mult: Optional[float] = None,
    ) -> Optional[Signal]:
        if df.empty or len(df) < self.cfg.ema_slow + 5:
            return None

        if volume_spike_mult is None:
            volume_spike_mult = self.cfg.volume_spike_mult

        ema_fast = df["ema_fast"].iloc[-1]
        ema_slow = df["ema_slow"].iloc[-1]
        ema_fast_prev = df["ema_fast"].iloc[-2]
        ema_slow_prev = df["ema_slow"].iloc[-2]

        rsi = df["rsi"].iloc[-1]

        vol = df["volume"].iloc[-1]
        vol_ma = df["volume"].rolling(window=20).mean().iloc[-1]

        side: Optional[Side] = None

        # Long: EMA fast crosses above slow
        if ema_fast_prev <= ema_slow_prev and ema_fast > ema_slow:
            if self.cfg.rsi_lower <= rsi <= self.cfg.rsi_upper:
                if vol >= volume_spike_mult * vol_ma:
                    side = Side.LONG

        # Short: EMA fast crosses below slow
        elif ema_fast_prev >= ema_slow_prev and ema_fast < ema_slow:
            if self.cfg.rsi_lower <= rsi <= self.cfg.rsi_upper:
                if vol >= volume_spike_mult * vol_ma:
                    side = Side.SHORT

        if side is None:
            return None

        stop_loss, take_profit = self._compute_stops(current_price, side)

        return Signal(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            side=side,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strength=1.0,
            metadata={
                "rsi": float(rsi),
                "ema_fast": float(ema_fast),
                "ema_slow": float(ema_slow),
                "volume_ratio": float(vol / vol_ma) if vol_ma > 0 else 1.0,
            },
        )

    def _compute_stops(self, price: float, side: Side) -> tuple[float, float]:
        sl_pct = self.cfg.stop_loss_pct
        tp_pct = self.cfg.take_profit_pct

        if side == Side.LONG:
            stop_loss = price * (1 - sl_pct)
            take_profit = price * (1 + tp_pct)
        else:
            stop_loss = price * (1 + sl_pct)
            take_profit = price * (1 - tp_pct)

        return stop_loss, take_profit
