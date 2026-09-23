"""
Mean Reversion Strategy for Crypto

Logic:
- Buy when RSI < 30 (oversold) AND price below lower Bollinger Band
- Sell when RSI > 70 (overbought) AND price above upper Bollinger Band
- Exit when RSI crosses back through 50 (mean reached)
- Tight stops because mean reversion either works quickly or fails

Best for: Ranging/choppy markets (70% of crypto time)
Worst for: Strong trending markets (use trend strategy instead)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd
import ta.volatility as ta_volatility
import ta.momentum as ta_momentum

from trend_scalper.models import Signal, Side
from trend_scalper.config import StrategyConfig


class MeanReversionStrategy:
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        # Mean reversion specific parameters
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.rsi_mean = 50
        self.bb_window = 20
        self.bb_std = 2.0
        self.stop_loss_pct = 0.005  # 0.5% - tight because failures are quick
        self.take_profit_pct = 0.015  # 1.5% - let winners run to mean

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add RSI and Bollinger Bands to dataframe."""
        df = df.copy()
        
        # RSI
        df['rsi'] = ta_momentum.rsi(df['close'], window=self.cfg.rsi_period)
        
        # Bollinger Bands
        indicator = ta_volatility.BollingerBands(
            close=df['close'],
            window=self.bb_window,
            window_dev=self.bb_std
        )
        df['bb_upper'] = indicator.bollinger_hband()
        df['bb_middle'] = indicator.bollinger_mavg()
        df['bb_lower'] = indicator.bollinger_lband()
        
        return df

    def generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        current_price: float,
        volume_spike_mult: Optional[float] = None,
    ) -> Optional[Signal]:
        """
        Generate mean reversion signal.
        
        LONG: RSI < 30 AND price < lower BB
        SHORT: RSI > 70 AND price > upper BB
        """
        if df.empty or len(df) < self.bb_window + 5:
            return None

        # Add indicators
        df = self._add_indicators(df)
        
        rsi = df['rsi'].iloc[-1]
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        bb_middle = df['bb_middle'].iloc[-1]

        # Volume filter (optional)
        if volume_spike_mult:
            vol = df['volume'].iloc[-1]
            vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
            if vol_ma > 0 and vol < volume_spike_mult * vol_ma:
                # Volume too low - skip trade
                return None

        side: Optional[Side] = None

        # LONG: Oversold + below lower band
        if rsi < self.rsi_oversold and current_price < bb_lower:
            side = Side.LONG

        # SHORT: Overbought + above upper band
        elif rsi > self.rsi_overbought and current_price > bb_upper:
            side = Side.SHORT

        if side is None:
            return None

        # Calculate stops and targets
        if side == Side.LONG:
            # Stop below recent low
            stop_loss = current_price * (1 - self.stop_loss_pct)
            # Target at middle band (mean)
            take_profit = bb_middle
        else:  # SHORT
            # Stop above recent high
            stop_loss = current_price * (1 + self.stop_loss_pct)
            # Target at middle band (mean)
            take_profit = bb_middle

        return Signal(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            side=side,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strength=1.0,
            metadata={
                'rsi': float(rsi),
                'bb_upper': float(bb_upper),
                'bb_middle': float(bb_middle),
                'bb_lower': float(bb_lower),
                'distance_to_mean': abs(current_price - bb_middle) / bb_middle,
            },
        )