from decimal import Decimal

from src.execution.quote_engine import QuoteEngine
from src.execution.order_manager import OrderManager, OrderParams
from src.execution.slippage_controller import SlippageController


class FakeInfo:
    def l2_snapshot(self, name: str):
        return {
            "levels": [
                [{"px": 10.0, "sz": 1}],
                [{"px": 10.02, "sz": 1}],
            ]
        }


class FakeGateway:
    def __init__(self):
        self.info = FakeInfo()
        self.placed = []

    def get_symbol_meta(self, symbol: str):
        from src.exchanges.base_gateway import SymbolMeta

        return SymbolMeta(symbol=symbol, venue="test", kind="spot", tick=Decimal("0.01"), size_decimals=2)

    def get_l2(self, symbol: str):
        return self.info.l2_snapshot(symbol)

    def place_order(self, symbol, side, qty, price, tif="Gtc", reduce_only=False, post_only=True):
        self.placed.append((symbol, side, float(qty), float(price), tif, reduce_only, post_only))
        return {"ok": True}

    def cancel_order(self, symbol, oid):
        return {"ok": True}


def test_quote_engine_prices_and_qty():
    gw = FakeGateway()
    qe = QuoteEngine(gw, "ASTER/USDT")
    buy_px = qe.passive_buy_price(1)
    sell_px = qe.passive_sell_price(1)
    assert buy_px < sell_px
    qty = qe.base_qty_from_usd(Decimal("200"))
    assert qty > 0


def test_order_manager_place_and_cancel():
    gw = FakeGateway()
    om = OrderManager(gw, "ASTER/USDT", OrderParams(tif="Gtc", post_only=True))
    resp = om.place_limit("BUY", Decimal("1.00"), Decimal("10.00"))
    assert resp["ok"] is True
    assert len(gw.placed) == 1


def test_slippage_controller():
    sc = SlippageController(max_bps=50)  # 0.5%
    ref = Decimal("100")
    # buy capped to +0.5
    assert sc.enforce(ref, Decimal("101"), is_buy=True) == Decimal("100.5")
    # sell capped to -0.5
    assert sc.enforce(ref, Decimal("98"), is_buy=False) == Decimal("99.5")


