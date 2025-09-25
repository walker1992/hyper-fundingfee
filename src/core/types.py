from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional


@dataclass
class Market:
    symbol: str
    venue: str
    kind: str
    tick: Decimal
    size_decimals: int
    min_qty: Optional[Decimal] = None
    min_notional: Optional[Decimal] = None


@dataclass
class Quote:
    bid: Decimal
    ask: Decimal
    ts: float


@dataclass
class Funding:
    symbol: str
    next_rate: Decimal
    next_ts: float
    last_rate: Optional[Decimal] = None
    window_seconds: int = 3600


@dataclass
class Order:
    oid: Optional[int]
    symbol: str
    side: str
    qty: Decimal
    price: Optional[Decimal]
    tif: str
    flags: Dict[str, bool]
    status: str


@dataclass
class Position:
    symbol: str
    base: Decimal
    avg_price: Decimal
    realized_pnl: Decimal
    funding_accrual: Decimal


