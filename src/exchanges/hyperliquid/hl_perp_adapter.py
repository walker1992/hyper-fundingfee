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


class HyperliquidPerpAdapter(ExchangeGateway):
    def __init__(self, address: str, info: Info, exchange: Exchange) -> None:
        self.address = address
        self.info = info
        self.exchange = exchange

    def normalize_symbol(self, raw_symbol: str) -> str:
        candidate = raw_symbol.strip()
        if candidate in self.info.name_to_coin:
            return candidate
        upper = candidate.upper()
        if upper in self.info.name_to_coin:
            return upper
        for suffix in ("USDT", "USDC", "USD"):
            if upper.endswith(suffix) and len(upper) > len(suffix):
                base = upper[: -len(suffix)]
                if base in self.info.name_to_coin:
                    return base
        raise ValueError(candidate)

    def get_symbol_meta(self, symbol: str) -> SymbolMeta:
        asset = self.info.name_to_asset(symbol)
        size_decimals = int(self.info.asset_to_sz_decimals[asset])
        tick = infer_tick_from_l2(self.info, symbol)
        if tick is None:
            price_decimals = max(0, 6 - size_decimals)
            tick = Decimal(1).scaleb(-price_decimals)
        return SymbolMeta(symbol=symbol, venue="hyperliquid", kind="perp", tick=tick, size_decimals=size_decimals)

    def get_l2(self, symbol: str) -> Dict[str, Any]:
        return self.info.l2_snapshot(symbol)

    def get_balances(self) -> Dict[str, Any]:
        return self.info.user_state(self.address)

    def get_positions(self) -> Dict[str, Any]:
        return self.info.user_state(self.address)

    def get_open_orders(self) -> Any:
        return self.info.frontend_open_orders(self.address)

    def get_funding(self, symbol: str) -> Dict[str, Any]:
        # Funding history endpoint; latest funding can be derived from metaAndAssetCtxs
        ctx = self.info.meta_and_asset_ctxs()
        # meta_and_asset_ctxs returns [meta, assetCtxs]
        asset_ctxs = ctx[1] if isinstance(ctx, list) and len(ctx) > 1 else []
        latest = None
        for m, a in zip(ctx[0]["universe"], asset_ctxs):
            if m.get("name") == symbol:
                latest = a
                break
        return {
            "symbol": symbol,
            "funding": latest.get("funding") if isinstance(latest, dict) else None,
            "markPx": latest.get("markPx") if isinstance(latest, dict) else None,
        }

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
        order_type: Dict[str, Any] = {"limit": {"tif": tif}}
        if post_only:
            order_type["limit"]["postOnly"] = True
        if reduce_only:
            order_type["reduceOnly"] = True
        try:
            # Try to set leverage to 1x before placing orders (idempotent on most venues)
            if hasattr(self.exchange, "configure_leverage"):
                try:
                    # Some SDKs use integer bps or float; we try both patterns
                    self.exchange.configure_leverage(symbol, 1)  # type: ignore[arg-type]
                except Exception:
                    try:
                        self.exchange.configure_leverage(symbol, 1.0)  # type: ignore[arg-type]
                    except Exception:
                        pass
        except Exception:
            pass
        return self.exchange.order(symbol, is_buy, float(qty), float(price), order_type)

    def cancel_order(self, symbol: str, oid: int) -> Any:
        return self.exchange.cancel(symbol, oid)

    # Convenience
    def best_bid_ask(self, symbol: str) -> tuple[Decimal, Decimal]:
        return best_bid_ask(self.info, symbol)


