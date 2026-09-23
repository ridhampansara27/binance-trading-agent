from __future__ import annotations

from datetime import UTC, datetime, timedelta

from risk_manager import RiskManager
from trend_scalper.models import Side, Signal


def _signal(symbol: str = "BTCUSDT") -> Signal:
    return Signal(
        timestamp=datetime.now(tz=UTC),
        symbol=symbol,
        side=Side.LONG,
        entry_price=100.0,
        stop_loss=99.0,
        take_profit=101.0,
    )


def test_daily_trade_cap_and_max_positions() -> None:
    manager = RiskManager({"trading": {"max_positions": 1, "max_trades_per_day": 1}})
    ok, _ = manager.can_open_position(_signal(), {})
    assert ok
    manager.record_entry()
    ok, reason = manager.can_open_position(_signal("ETHUSDT"), {})
    assert not ok
    assert reason == "daily-trade-cap-hit"


def test_correlation_guard_blocks_same_direction() -> None:
    manager = RiskManager(
        {
            "trading": {
                "correlation_guard_enabled": True,
                "correlation_guard_groups": [["BTCUSDT", "ETHUSDT", "SOLUSDT"]],
            }
        }
    )
    open_positions = {"BTCUSDT": {"side": "LONG"}}
    ok, reason = manager.can_open_position(_signal("ETHUSDT"), open_positions)
    assert not ok
    assert reason.startswith("correlation-guard:")


def test_symbol_cooldown_blocks_reentry() -> None:
    manager = RiskManager({"trading": {"cooldown_minutes": 30}})
    manager.last_closed_by_symbol["BTCUSDT"] = datetime.now(tz=UTC) - timedelta(minutes=5)
    ok, reason = manager.can_open_position(_signal("BTCUSDT"), {})
    assert not ok
    assert reason == "symbol-cooldown-active"
