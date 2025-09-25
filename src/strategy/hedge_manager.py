from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple

from src.exchanges.base_gateway import ExchangeGateway


@dataclass
class HedgeParams:
    target_usd_notional: Decimal
    hedge_ratio: Decimal


def _quantize_size(qty: Decimal, size_decimals: int) -> Decimal:
    quantum = Decimal(1).scaleb(-size_decimals)
    return (qty // quantum) * quantum


def _mid_price(gw: ExchangeGateway, symbol: str) -> Decimal:
    l2 = gw.get_l2(symbol)
    levels = l2.get("levels") or []
    bids = levels[0] if len(levels) > 0 else []
    asks = levels[1] if len(levels) > 1 else []
    bid = Decimal(str(bids[0]["px"])) if bids else Decimal("0")
    ask = Decimal(str(asks[0]["px"])) if asks else Decimal("0")
    if ask > 0:
        return (bid + ask) / Decimal(2)
    return bid


def compute_hedge_sizes(spot: ExchangeGateway, perp: ExchangeGateway, spot_symbol: str, perp_symbol: str, params: HedgeParams) -> Tuple[Decimal, Decimal]:
    spot_meta = spot.get_symbol_meta(spot_symbol)
    perp_meta = perp.get_symbol_meta(perp_symbol)
    spot_mid = _mid_price(spot, spot_symbol)
    if spot_mid <= 0:
        return Decimal("0"), Decimal("0")
    base_qty = (params.target_usd_notional / spot_mid) * params.hedge_ratio
    spot_qty = _quantize_size(base_qty, spot_meta.size_decimals)
    perp_qty = _quantize_size(base_qty, perp_meta.size_decimals)
    return spot_qty, perp_qty


