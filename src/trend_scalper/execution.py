from __future__ import annotations

from datetime import datetime
from typing import Optional

import structlog

from .models import Signal, Order, Side, OrderType, Trade, Position
from .exchange import ExchangeClient
from .risk import RiskManager
from .config import TradingConfig

logger = structlog.get_logger()


class ExecutionEngine:
    def __init__(self, exchange: ExchangeClient, risk: RiskManager, cfg: TradingConfig):
        self.exchange = exchange
        self.risk = risk
        self.cfg = cfg
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []

    def can_execute_signal(self, signal: Signal) -> bool:
        if signal.symbol in self.positions:
            logger.debug("Position already open for symbol", symbol=signal.symbol)
            return False
        if not self.risk.can_open_position():
            logger.debug("Risk manager disallows new position", symbol=signal.symbol)
            return False
        return True

    def build_order_from_signal(self, signal: Signal, qty: float) -> Order:
        return Order(
            symbol=signal.symbol,
            side=signal.side,
            order_type=OrderType.MARKET,
            quantity=qty,
            price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            metadata={"signal_ts": signal.timestamp},
        )

    async def execute_signal(self, signal: Signal, qty: float) -> Optional[Trade]:
        if not self.can_execute_signal(signal):
            return None

        order = self.build_order_from_signal(signal, qty)
        filled_order = await self.exchange.submit_order(order)

        if filled_order.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            logger.warning("Order not filled", order=filled_order)
            return None

        position = Position(
            symbol=signal.symbol,
            side=signal.side,
            quantity=filled_order.filled_qty,
            entry_price=filled_order.avg_fill_price or signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            opened_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        self.positions[signal.symbol] = position
        self.risk.increment_open_positions()

        trade = Trade(
            symbol=signal.symbol,
            side=signal.side,
            entry_price=position.entry_price,
            quantity=position.quantity,
            entry_time=datetime.utcnow(),
            metadata={"order_id": filled_order.order_id},
        )
        self.trades.append(trade)

        logger.info(
            "Position opened",
            symbol=signal.symbol,
            side=signal.side.value,
            qty=position.quantity,
            entry=position.entry_price,
        )

        return trade

    async def close_position(self, symbol: str, reason: str = "manual") -> Optional[Trade]:
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        side = Side.SHORT if pos.side == Side.LONG else Side.LONG

        close_order = Order(
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=pos.quantity,
            metadata={"close_reason": reason},
        )

        filled = await self.exchange.submit_order(close_order)
        if filled.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            logger.warning("Close order not filled", order=filled)
            return None

        exit_price = filled.avg_fill_price or pos.entry_price

        pnl = (exit_price - pos.entry_price) * pos.quantity
        if pos.side == Side.SHORT:
            pnl = -pnl

        fees = abs(pnl) * self.cfg.taker_fee
        slippage = abs(pnl) * (self.cfg.slippage_bps / 10000)

        trade = Trade(
            symbol=symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            pnl=pnl - fees - slippage,
            fees=fees,
            slippage_cost=slippage,
            entry_time=pos.opened_at,
            exit_time=datetime.utcnow(),
            metadata={"close_reason": reason, "order_id": filled.order_id},
        )

        self.trades.append(trade)
        del self.positions[symbol]
        self.risk.decrement_open_positions()
        self.risk.update_daily_pnl(trade.pnl)

        logger.info(
            "Position closed",
            symbol=symbol,
            side=pos.side.value,
            qty=pos.quantity,
            entry=pos.entry_price,
            exit=exit_price,
            pnl=trade.pnl,
        )

        return trade

    async def check_stops_and_targets(self, current_prices: dict[str, float]) -> list[Trade]:
        closed: list[Trade] = []
        for symbol, pos in list(self.positions.items()):
            price = current_prices.get(symbol)
            if price is None:
                continue

            should_close = False
            reason = ""

            if pos.stop_loss is not None:
                if pos.side == Side.LONG and price <= pos.stop_loss:
                    should_close = True
                    reason = "stop_loss"
                elif pos.side == Side.SHORT and price >= pos.stop_loss:
                    should_close = True
                    reason = "stop_loss"

            if not should_close and pos.take_profit is not None:
                if pos.side == Side.LONG and price >= pos.take_profit:
                    should_close = True
                    reason = "take_profit"
                elif pos.side == Side.SHORT and price <= pos.take_profit:
                    should_close = True
                    reason = "take_profit"

            if should_close:
                trade = await self.close_position(symbol, reason=reason)
                if trade:
                    closed.append(trade)

        return closed
