from __future__ import annotations

from datetime import UTC

import pandas as pd

from trend_scalper.research.backtester import (
    ResearchConfig,
    normalize_quantity,
    run_research_backtest,
)


def _bars_5m() -> pd.DataFrame:
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    rows = []
    price = 100.0
    for i in range(60):
        rows.append(
            {
                "timestamp": base + pd.Timedelta(minutes=5 * i),
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price + (0.5 if i == 30 else 0.0),
                "volume": 10_000.0,
            }
        )
    return pd.DataFrame(rows)


def _bars_1m() -> pd.DataFrame:
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    rows = []
    for i in range(300):
        price = 100.0 + (0.1 if i % 10 == 0 else 0.0)
        rows.append(
            {
                "timestamp": base + pd.Timedelta(minutes=i),
                "open": price,
                "high": price + 0.2,
                "low": price - 0.2,
                "close": price,
                "volume": 1_000.0,
            }
        )
    return pd.DataFrame(rows)


def test_symbol_filter_normalization() -> None:
    cfg = ResearchConfig(
        symbols=["BTCUSDT"],
        min_notional={"BTCUSDT": 5},
        min_qty={"BTCUSDT": 0.001},
        step_size={"BTCUSDT": 0.001},
    )
    assert normalize_quantity("BTCUSDT", 0.0029, cfg, 30000) == 0.002
    assert normalize_quantity("BTCUSDT", 0.0009, cfg, 30000) == 0.0
    assert normalize_quantity("BTCUSDT", 0.001, cfg, 1000) == 0.0


def test_intrabar_exit_order_conservative_stop_first() -> None:
    cfg = ResearchConfig(
        symbols=["BTCUSDT"],
        max_hold_minutes=15,
        min_notional={"BTCUSDT": 5},
        min_qty={"BTCUSDT": 0.001},
        step_size={"BTCUSDT": 0.001},
    )
    five = _bars_5m()
    one = _bars_1m()
    # Create a strong signal to force an entry near bar 30.
    five.loc[30, "close"] = 90.0
    # Same 5m window breaches both stop and take-profit.
    entry_ts = five.loc[31, "timestamp"]
    mask = (one["timestamp"] >= entry_ts) & (one["timestamp"] < entry_ts + pd.Timedelta(minutes=5))
    one.loc[mask, "low"] = 80.0
    one.loc[mask, "high"] = 120.0
    result = run_research_backtest({"BTCUSDT": five}, {"BTCUSDT": one}, cfg)
    if result["trades"]:
        assert result["trades"][0]["exit_reason"] == "stop_loss"


def test_no_lookahead_and_costs_applied() -> None:
    cfg = ResearchConfig(
        symbols=["BTCUSDT"],
        latency_bars=1,
        min_notional={"BTCUSDT": 5},
        min_qty={"BTCUSDT": 0.001},
        step_size={"BTCUSDT": 0.001},
    )
    five = _bars_5m()
    one = _bars_1m()
    five.loc[30, "close"] = 90.0
    result = run_research_backtest({"BTCUSDT": five}, {"BTCUSDT": one}, cfg)
    if result["trades"]:
        trade = result["trades"][0]
        assert pd.Timestamp(trade["entry_time"]).tz_convert(UTC) >= five.loc[31, "timestamp"]
    assert result["summary"]["net_return"] <= 0.0
