from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

from src.exchanges.base_gateway import ExchangeGateway


@dataclass
class OrderParams:
    tif: str = "Gtc"
    post_only: bool = True
    reduce_only: bool = False


class OrderManager:
    def __init__(self, gateway: ExchangeGateway, symbol: str, params: Optional[OrderParams] = None) -> None:
        self.gw = gateway
        self.symbol = symbol
        self.params = params or OrderParams()

    def place_limit(self, side: str, qty: Decimal, price: Decimal) -> Any:
        return self.gw.place_order(
            self.symbol,
            side,
            qty,
            price,
            tif=self.params.tif,
            reduce_only=self.params.reduce_only,
            post_only=self.params.post_only,
        )

    def cancel(self, oid: int) -> Any:
        return self.gw.cancel_order(self.symbol, oid)


