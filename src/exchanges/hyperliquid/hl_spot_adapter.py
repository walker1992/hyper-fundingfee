from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

try:  # Allow tests to run without the SDK installed
    from hyperliquid.info import Info  # type: ignore
    from hyperliquid.exchange import Exchange  # type: ignore
except Exception:  # pragma: no cover - fallback for environments without SDK
    Info = Any  # type: ignore
    Exchange = Any  # type: ignore

from src.exchanges.base_gateway import ExchangeGateway, SymbolMeta
from src.exchanges.hyperliquid.hl_common import best_bid_ask, infer_tick_from_l2


class HyperliquidSpotAdapter(ExchangeGateway):
    def __init__(self, address: str, info: Info, exchange: Exchange) -> None:
        self.address = address
        self.info = info
        self.exchange = exchange

    def normalize_symbol(self, raw_symbol: str) -> str:
        candidate = raw_symbol.strip()
        if "/" in candidate:
            if candidate in self.info.name_to_coin:
                return candidate
        else:
            for quote in ("USDC", "USDT", "USD"):
                if candidate.endswith(quote) and len(candidate) > len(quote):
                    base = candidate[: -len(quote)]
                    pair = f"{base}/{quote}"
                    if pair in self.info.name_to_coin:
                        return pair
        if candidate in self.info.name_to_coin:
            return candidate
        raise ValueError(candidate)

    def get_symbol_meta(self, symbol: str) -> SymbolMeta:
        asset = self.info.name_to_asset(symbol)
        size_decimals = int(self.info.asset_to_sz_decimals[asset])
        tick = infer_tick_from_l2(self.info, symbol)
        if tick is None:
            price_decimals = max(0, 8 - size_decimals)
            tick = Decimal(1).scaleb(-price_decimals)
        return SymbolMeta(symbol=symbol, venue="hyperliquid", kind="spot", tick=tick, size_decimals=size_decimals)

    def get_l2(self, symbol: str) -> Dict[str, Any]:
        return self.info.l2_snapshot(symbol)

    def get_balances(self) -> Dict[str, Any]:
        return self.info.spot_user_state(self.address)

    def get_positions(self) -> Dict[str, Any]:
        # Spot does not maintain leverage positions; return balances summary
        return self.get_balances()

    def get_open_orders(self) -> Any:
        return self.info.frontend_open_orders(self.address)

    def get_funding(self, symbol: str) -> Dict[str, Any]:
        # No funding for spot; return empty info for interface compatibility
        return {"symbol": symbol, "next_rate": None, "next_ts": None}

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
        is_buy = side.upper() == "BUY"
        order_type = {"limit": {"tif": tif}}
        if post_only:
            order_type["limit"]["postOnly"] = True
        return self.exchange.order(symbol, is_buy, float(qty), float(price), order_type)

    def cancel_order(self, symbol: str, oid: int) -> Any:
        return self.exchange.cancel(symbol, oid)

    # Convenience
    def best_bid_ask(self, symbol: str) -> tuple[Decimal, Decimal]:
        return best_bid_ask(self.info, symbol)


