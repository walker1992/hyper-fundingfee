from decimal import Decimal

import pytest

from src.exchanges.hyperliquid.hl_spot_adapter import HyperliquidSpotAdapter
from src.exchanges.hyperliquid.hl_perp_adapter import HyperliquidPerpAdapter


class FakeInfo:
    def __init__(self):
        # minimal symbol mappings
        self.name_to_coin = {"ASTER": "ASTER", "ASTER/USDT": "@8"}
        self.coin_to_asset = {"ASTER": 1, "@8": 10001}
        self.asset_to_sz_decimals = {1: 2, 10001: 3}

    def name_to_asset(self, name: str) -> int:
        return self.coin_to_asset[self.name_to_coin[name]] if name in self.name_to_coin else self.coin_to_asset[name]

    def l2_snapshot(self, name: str):
        return {
            "levels": [
                [{"px": 10.0, "sz": 1}],
                [{"px": 10.02, "sz": 1}],
            ]
        }

    def frontend_open_orders(self, address: str):
        return []

    def spot_user_state(self, address: str):
        return {"balances": []}

    def user_state(self, address: str):
        return {"marginSummary": {}}

    def meta_and_asset_ctxs(self):
        return [
            {"universe": [{"name": "ASTER", "szDecimals": 2}]},
            [{"funding": "0.0001", "markPx": "10.01"}],
        ]


class FakeExchange:
    def __init__(self):
        self.placed = []
        self.canceled = []

    def order(self, symbol, is_buy, qty, px, order_type):
        self.placed.append((symbol, is_buy, qty, px, order_type))
        return {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 1}}]}}}

    def cancel(self, symbol, oid):
        self.canceled.append((symbol, oid))
        return {"status": "ok", "response": {"data": {"statuses": ["Cancelled"]}}}


@pytest.fixture()
def fakes():
    info = FakeInfo()
    ex = FakeExchange()
    address = "0xabc"
    return address, info, ex


def test_spot_meta_and_order_flow(fakes):
    address, info, ex = fakes
    spot = HyperliquidSpotAdapter(address, info, ex)
    symbol = spot.normalize_symbol("ASTERUSDT")
    meta = spot.get_symbol_meta(symbol)
    assert meta.kind == "spot"
    assert meta.tick > Decimal("0")

    l2 = spot.get_l2(symbol)
    assert "levels" in l2

    balances = spot.get_balances()
    assert "balances" in balances

    resp = spot.place_order(symbol, "BUY", Decimal("1"), Decimal("10.00"), tif="Gtc", post_only=True)
    assert resp["status"] == "ok"
    cancel = spot.cancel_order(symbol, 1)
    assert cancel["status"] == "ok"


def test_perp_meta_funding_and_order_flow(fakes):
    address, info, ex = fakes
    perp = HyperliquidPerpAdapter(address, info, ex)
    symbol = perp.normalize_symbol("aster")
    meta = perp.get_symbol_meta(symbol)
    assert meta.kind == "perp"
    assert meta.tick > Decimal("0")

    funding = perp.get_funding(symbol)
    assert funding["symbol"] == "ASTER"

    resp = perp.place_order(symbol, "SELL", Decimal("1"), Decimal("10.02"), reduce_only=True)
    assert resp["status"] == "ok"
    cancel = perp.cancel_order(symbol, 1)
    assert cancel["status"] == "ok"


