"""Local risk checks for the testnet trading bot."""
from __future__ import annotations

from datetime import date

from trend_scalper.models import Signal


class RiskManager:
    def __init__(self, config: dict):
        trading = config.get("trading", {})
        self.risk_per_trade = float(trading.get("risk_per_trade", 0.005))
        self.max_daily_loss_pct = float(trading.get("max_daily_loss_pct", 0.02))
        self.daily_pnl_pct = 0.0
        self.current_day = date.today()

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self.current_day:
            self.current_day = today
            self.daily_pnl_pct = 0.0

    def can_open_position(self, signal: Signal) -> bool:
        self._reset_if_new_day()
        if signal.entry_price <= 0:
            return False
        if signal.stop_loss <= 0 or signal.take_profit <= 0:
            return False
        if abs(signal.entry_price - signal.stop_loss) <= 0:
            return False
        if self.daily_pnl_pct <= -self.max_daily_loss_pct:
            return False
        return True

    def record_trade_return(self, pnl_pct: float) -> None:
        self._reset_if_new_day()
        self.daily_pnl_pct += pnl_pct
