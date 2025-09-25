from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple

from src.exchanges.base_gateway import ExchangeGateway


def _best_bid_ask(gw: ExchangeGateway, symbol: str) -> Tuple[Decimal, Decimal]:
    l2 = gw.get_l2(symbol)
    levels = l2.get("levels") or []
    bids = levels[0] if len(levels) > 0 else []
    asks = levels[1] if len(levels) > 1 else []
    bid = Decimal(str(bids[0]["px"])) if bids else Decimal("0")
    ask = Decimal(str(asks[0]["px"])) if asks else Decimal("0")
    return bid, ask


def _quantize_size(qty: Decimal, size_decimals: int) -> Decimal:
    quantum = Decimal(1).scaleb(-size_decimals)
    return (qty // quantum) * quantum


@dataclass
class QuoteParams:
    price_offset_ticks: int = 1


class QuoteEngine:
    def __init__(self, gateway: ExchangeGateway, symbol: str) -> None:
        self.gw = gateway
        self.symbol = symbol
        self.meta = self.gw.get_symbol_meta(symbol)

    def passive_buy_price(self, offset_ticks: int) -> Decimal:
        bid, ask = _best_bid_ask(self.gw, self.symbol)
        px = bid + self.meta.tick * Decimal(offset_ticks)
        if ask > 0:
            spread = ask - bid
            # Avoid equality with passive sell when spread == 2 * tick by falling back to bid
            if spread <= self.meta.tick * Decimal(2) and px >= ask - self.meta.tick:
                return bid
            if px >= ask:
                px = max(bid, ask - self.meta.tick)
        return px

    def passive_sell_price(self, offset_ticks: int) -> Decimal:
        bid, ask = _best_bid_ask(self.gw, self.symbol)
        px = ask - self.meta.tick * Decimal(offset_ticks)
        if bid > 0 and px <= bid:
            px = min(ask, bid + self.meta.tick)
        return px

    def base_qty_from_usd(self, target_usd_notional: Decimal) -> Decimal:
        bid, ask = _best_bid_ask(self.gw, self.symbol)
        mid = (bid + ask) / Decimal(2) if ask > 0 else bid
        if mid <= 0:
            return Decimal("0")
        raw = target_usd_notional / mid
        return _quantize_size(raw, self.meta.size_decimals)


