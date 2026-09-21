from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: int


@dataclass
class Signal:
    timestamp: datetime
    symbol: str
    side: Side
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    strength: float = 1.0
    metadata: dict = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    side: Side
    quantity: float
    entry_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    opened_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Order:
    symbol: str
    side: Side
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.NEW
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)


@dataclass
class Trade:
    symbol: str
    side: Side
    entry_price: float
    exit_price: Optional[float] = None
    quantity: float = 0.0
    pnl: float = 0.0
    fees: float = 0.0
    slippage_cost: float = 0.0
    funding: float = 0.0
    entry_time: datetime = field(default_factory=datetime.utcnow)
    exit_time: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class AccountState:
    equity: float
    available_balance: float
    total_position_initial_margin: float
    unrealized_pnl: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
