from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from .models import Signal, Side
from .config import TradingConfig


@dataclass
class RiskState:
    equity: float
    daily_pnl: float = 0.0
    open_positions: int = 0
    last_reset_date: date = field(default_factory=lambda: date.today())


class RiskManager:
    def __init__(self, cfg: TradingConfig):
        self.cfg = cfg
        self.state = RiskState(equity=10000.0)

    def update_equity(self, equity: float) -> None:
        self.state.equity = equity
        today = date.today()
        if today != self.state.last_reset_date:
            self.state.daily_pnl = 0.0
            self.state.last_reset_date = today

    def update_daily_pnl(self, pnl: float) -> None:
        self.state.daily_pnl += pnl

    def can_open_position(self) -> bool:
        if self.state.open_positions >= self.cfg.max_open_positions:
            return False
        if self.state.daily_pnl <= -self.cfg.max_daily_loss * self.state.equity:
            return False
        return True

    def position_size_for_signal(self, signal: Signal, atr: Optional[float] = None) -> float:
        risk_amount = self.state.equity * self.cfg.risk_per_trade

        if signal.stop_loss is None:
            raise ValueError("Signal must have stop_loss for position sizing")

        risk_per_unit = abs(signal.entry_price - signal.stop_loss)
        if risk_per_unit <= 0:
            return 0.0

        qty = risk_amount / risk_per_unit
        return qty

    def increment_open_positions(self) -> None:
        self.state.open_positions += 1

    def decrement_open_positions(self) -> None:
        self.state.open_positions = max(0, self.state.open_positions - 1)
