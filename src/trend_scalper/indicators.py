from __future__ import annotations

import pandas as pd
import ta.trend as ta_trend
import ta.momentum as ta_momentum
import ta.volatility as ta_volatility


def compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    return ta_momentum.rsi(series, window=period)


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return ta_volatility.average_true_range(high, low, close, window=period)


def add_trend_indicators(df: pd.DataFrame, ema_fast: int = 9, ema_slow: int = 21) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = compute_ema(df["close"], ema_fast)
    df["ema_slow"] = compute_ema(df["close"], ema_slow)
    return df


def add_momentum_indicators(df: pd.DataFrame, rsi_period: int = 14) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = compute_rsi(df["close"], rsi_period)
    return df


def add_volatility_indicators(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    df = df.copy()
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], atr_period)
    return df
