"""Portfolio risk controls for the demo trading bot."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trend_scalper.models import Signal


class RiskManager:
    def __init__(self, config: dict):
        trading = config.get("trading", {})
        self.max_positions = int(trading.get("max_positions", 2))
        self.one_position_per_symbol = bool(trading.get("one_position_per_symbol", True))
        self.max_trades_per_day = int(trading.get("max_trades_per_day", 6))
        self.cooldown_minutes = int(trading.get("cooldown_minutes", 15))
        self.max_daily_loss_pct = float(trading.get("max_daily_loss_pct", 0.01))
        self.correlation_guard_enabled = bool(trading.get("correlation_guard_enabled", True))
        self.correlation_guard_groups = trading.get(
            "correlation_guard_groups",
            [["BTCUSDT", "ETHUSDT", "SOLUSDT"]],
        )
        self.daily_pnl_pct = 0.0
        self.daily_trade_count = 0
        self.current_day = datetime.now(tz=UTC).date()
        self.last_closed_by_symbol: dict[str, datetime] = {}

    def _reset_if_new_day(self) -> None:
        today = datetime.now(tz=UTC).date()
        if today != self.current_day:
            self.current_day = today
            self.daily_pnl_pct = 0.0
            self.daily_trade_count = 0
            self.last_closed_by_symbol.clear()

    def _is_correlated(self, a: str, b: str) -> bool:
        if a == b:
            return True
        for group in self.correlation_guard_groups:
            group_set = set(group)
            if a in group_set and b in group_set:
                return True
        return False

    def can_open_position(
        self,
        signal: Signal,
        open_positions: dict[str, dict],
    ) -> tuple[bool, str]:
        self._reset_if_new_day()
        if signal.entry_price <= 0:
            return False, "invalid-entry-price"
        if signal.stop_loss <= 0 or signal.take_profit <= 0:
            return False, "invalid-stop-or-target"
        if abs(signal.entry_price - signal.stop_loss) <= 0:
            return False, "invalid-stop-distance"
        if self.daily_pnl_pct <= -self.max_daily_loss_pct:
            return False, "daily-loss-limit-hit"
        if self.daily_trade_count >= self.max_trades_per_day:
            return False, "daily-trade-cap-hit"
        if len(open_positions) >= self.max_positions:
            return False, "max-positions-hit"
        if self.one_position_per_symbol and signal.symbol in open_positions:
            return False, "symbol-already-open"

        last_closed = self.last_closed_by_symbol.get(signal.symbol)
        if last_closed is not None and datetime.now(tz=UTC) - last_closed < timedelta(minutes=self.cooldown_minutes):
            return False, "symbol-cooldown-active"

        if self.correlation_guard_enabled:
            for symbol, position in open_positions.items():
                same_direction = position.get("side") == signal.side.value
                if same_direction and self._is_correlated(symbol, signal.symbol):
                    return False, f"correlation-guard:{symbol}"
        return True, "ok"

    def record_entry(self) -> None:
        self._reset_if_new_day()
        self.daily_trade_count += 1

    def record_trade_close(self, symbol: str, pnl_pct: float) -> None:
        self._reset_if_new_day()
        self.daily_pnl_pct += pnl_pct
        self.last_closed_by_symbol[symbol] = datetime.now(tz=UTC)
