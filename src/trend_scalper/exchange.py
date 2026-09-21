from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import structlog

try:
    from binance_sdk_derivatives_trading_usds_futures import UFMClient
except Exception:
    UFMClient = None  # type: ignore

from .config import TradingConfig
from .models import Order, Side, OrderType, OrderStatus, AccountState

logger = structlog.get_logger()


class ExchangeClient:
    def __init__(self, cfg: TradingConfig):
        self.cfg = cfg
        self.testnet = cfg.binance_testnet
        self.live_trading = cfg.live_trading

        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")

        if UFMClient is None:
            raise RuntimeError("binance-sdk-derivatives-trading-usds-futures not installed")

        if self.testnet:
            self.client = UFMClient(api_key=api_key or None, api_secret=api_secret or None, testnet=True)
        else:
            self.client = UFMClient(api_key=api_key or None, api_secret=api_secret or None, testnet=False)

    def is_configured(self) -> bool:
        if not self.testnet and not self.live_trading:
            return False
        if self.testnet and not os.getenv("BINANCE_API_KEY"):
            return False
        return True

    async def get_account_state(self) -> AccountState:
        return AccountState(
            equity=10000.0,
            available_balance=9000.0,
            total_position_initial_margin=0.0,
            unrealized_pnl=0.0,
            timestamp=datetime.utcnow(),
        )

    async def submit_order(self, order: Order) -> Order:
        if not self.is_configured():
            logger.warning("Exchange not configured; order not sent", order=order)
            order.status = OrderStatus.REJECTED
            order.metadata["reject_reason"] = "exchange_not_configured"
            return order

        if not self.live_trading and not self.testnet:
            logger.warning("Live trading disabled; order simulated", order=order)
            order.status = OrderStatus.FILLED
            order.filled_qty = order.quantity
            order.avg_fill_price = order.price or 0.0
            order.metadata["simulated"] = True
            return order

        try:
            symbol = order.symbol
            side = "BUY" if order.side == Side.LONG else "SELL"
            order_type = "MARKET" if order.order_type == OrderType.MARKET else "LIMIT"

            if order_type == "MARKET":
                resp = self.client.new_order(symbol=symbol, side=side, type=order_type, quantity=order.quantity)
            else:
                resp = self.client.new_order(
                    symbol=symbol, side=side, type=order_type, quantity=order.quantity, price=order.price
                )

            order_id = str(resp.get("orderId", ""))
            status_raw = resp.get("status", "NEW")
            status_map = {
                "NEW": OrderStatus.NEW,
                "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
                "FILLED": OrderStatus.FILLED,
                "CANCELED": OrderStatus.CANCELED,
                "REJECTED": OrderStatus.REJECTED,
                "EXPIRED": OrderStatus.EXPIRED,
            }
            order.order_id = order_id
            order.status = status_map.get(status_raw, OrderStatus.NEW)
            order.filled_qty = float(resp.get("executedQty", 0) or 0)
            order.avg_fill_price = float(resp.get("avgPrice", resp.get("price", 0)) or 0)
            order.updated_at = datetime.utcnow()
            order.metadata["raw_response"] = resp

        except Exception as e:
            logger.exception("Order submission failed", order=order, error=str(e))
            order.status = OrderStatus.REJECTED
            order.metadata["reject_reason"] = str(e)

        return order

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        if not self.is_configured():
            return False
        try:
            self.client.cancel_order(symbol=symbol, orderid=order_id)
            return True
        except Exception:
            return False

    async def get_open_positions(self) -> list[dict]:
        if not self.is_configured():
            return []
        try:
            positions = self.client.get_position_info()
            return positions if isinstance(positions, list) else []
        except Exception:
            return []
