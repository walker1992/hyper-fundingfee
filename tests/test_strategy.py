from decimal import Decimal

from src.strategy.funding_carry import FundingCarryStrategy, StrategyConfig


class FakeInfo:
    def __init__(self, funding_value: str = "0.0002"):
        self.funding_value = funding_value

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
            [{"funding": self.funding_value, "markPx": "10.01"}],
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


class FakeSpotGateway:
    def __init__(self, address: str, info: FakeInfo, ex: FakeExchange):
        self.address = address
        self.info = info
        self.exchange = ex

    def normalize_symbol(self, raw_symbol: str) -> str:
        return "ASTER/USDT"

    def get_symbol_meta(self, symbol: str):
        from src.exchanges.base_gateway import SymbolMeta

        return SymbolMeta(symbol=symbol, venue="hyperliquid", kind="spot", tick=Decimal("0.01"), size_decimals=2)

    def get_l2(self, symbol: str):
        return self.info.l2_snapshot(symbol)

    def get_balances(self):
        return self.info.spot_user_state(self.address)

    def get_positions(self):
        return self.get_balances()

    def get_open_orders(self):
        return self.info.frontend_open_orders(self.address)

    def get_funding(self, symbol: str):
        return {"symbol": symbol, "next_rate": None, "next_ts": None}

    def place_order(self, symbol: str, side: str, qty: Decimal, price: Decimal, tif: str = "Gtc", reduce_only: bool = False, post_only: bool = False):
        is_buy = side.upper() == "BUY"
        order_type = {"limit": {"tif": tif}}
        if post_only:
            order_type["limit"]["postOnly"] = True
        return self.exchange.order(symbol, is_buy, float(qty), float(price), order_type)

    def cancel_order(self, symbol: str, oid: int):
        return self.exchange.cancel(symbol, oid)


class FakePerpGateway(FakeSpotGateway):
    def get_symbol_meta(self, symbol: str):
        from src.exchanges.base_gateway import SymbolMeta

        return SymbolMeta(symbol=symbol, venue="hyperliquid", kind="perp", tick=Decimal("0.01"), size_decimals=2)

    def get_funding(self, symbol: str):
        # mirror hl_perp_adapter.get_funding simplified structure
        return {"symbol": symbol, "funding": self.info.funding_value, "markPx": "10.01"}


def test_enter_on_positive_funding():
    address = "0xabc"
    info = FakeInfo(funding_value="0.20")
    ex = FakeExchange()
    spot = FakeSpotGateway(address, info, ex)
    perp = FakePerpGateway(address, info, ex)

    cfg = StrategyConfig(
        enter_threshold_apr=Decimal("0.10"),
        exit_threshold_apr=Decimal("0.04"),
        target_usd_notional=Decimal("200"),
        hedge_ratio=Decimal("1.0"),
        price_offset_ticks=1,
        tif="Gtc",
        post_only=True,
    )
    strat = FundingCarryStrategy(spot, perp, "ASTER/USDT", "ASTER", cfg)
    result = strat.evaluate_and_place()
    assert result["entered"] is True
    assert len(ex.placed) == 2


def test_no_entry_below_threshold():
    address = "0xabc"
    info = FakeInfo(funding_value="0.01")
    ex = FakeExchange()
    spot = FakeSpotGateway(address, info, ex)
    perp = FakePerpGateway(address, info, ex)

    cfg = StrategyConfig(
        enter_threshold_apr=Decimal("0.10"),
        exit_threshold_apr=Decimal("0.04"),
        target_usd_notional=Decimal("200"),
        hedge_ratio=Decimal("1.0"),
        price_offset_ticks=1,
        tif="Gtc",
        post_only=True,
    )
    strat = FundingCarryStrategy(spot, perp, "ASTER/USDT", "ASTER", cfg)
    result = strat.evaluate_and_place()
    assert result["entered"] is False
    assert len(ex.placed) == 0


