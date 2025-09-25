from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, Optional


@dataclass
class SymbolMeta:
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


class ExchangeGateway(ABC):
    @abstractmethod
    def normalize_symbol(self, raw_symbol: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_symbol_meta(self, symbol: str) -> SymbolMeta:
        raise NotImplementedError

    @abstractmethod
    def get_l2(self, symbol: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_balances(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_open_orders(self) -> Any:
        raise NotImplementedError

    @abstractmethod
    def get_funding(self, symbol: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        tif: str = "Gtc",
        reduce_only: bool = False,
        post_only: bool = False,
    ) -> Any:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, symbol: str, oid: int) -> Any:
        raise NotImplementedError


