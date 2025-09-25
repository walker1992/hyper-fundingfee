import os
import sys
import json
import decimal

import eth_account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.exchanges.hyperliquid.hl_spot_adapter import HyperliquidSpotAdapter  # noqa: E402
from src.exchanges.hyperliquid.hl_perp_adapter import HyperliquidPerpAdapter  # noqa: E402


CONFIG_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "config.json"))


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_wallet(config: dict):
    secret_key = config.get("secret_key")
    if not secret_key:
        raise RuntimeError("secret_key missing in config.json")
    account = eth_account.Account.from_key(secret_key)
    address = account.address
    return address, account


def _setup_clients(config: dict):
    base_url = config.get("base_url") or constants.TESTNET_API_URL
    address, account = _load_wallet(config)
    info = Info(base_url, skip_ws=True)
    exchange = Exchange(account, base_url, account_address=address)
    return address, account, info, exchange


def _quantize(value: decimal.Decimal, decimals: int, rounding=decimal.ROUND_DOWN) -> decimal.Decimal:
    exp = decimal.Decimal(1).scaleb(-decimals)
    return value.quantize(exp, rounding=rounding)


def run():
    config = _load_config()
    address, account, info, exchange = _setup_clients(config)

    print(f"CONFIG_PATH={CONFIG_PATH}")
    print(f"Main wallet: {address}")
    print(f"API wallet:  {account.address}")
    print(f"Base URL:    {config.get('base_url') or constants.TESTNET_API_URL}")
    print("Connectivity: fetching spot_meta and meta...")
    sm = info.spot_meta()
    pm = info.meta()
    print(f"spot_meta tokens={len(sm.get('tokens', []))} perp_universe={len(pm.get('universe', []))}")

    try:
        us = info.user_state(address)
        sus = info.spot_user_state(address)
        print(f"user_state.ok={isinstance(us, dict)} spot_user_state.ok={isinstance(sus, dict)}")
    except Exception as e:
        print(f"[WARN] user_state check failed: {e}")

    spot_symbol = config.get("spot_symbol")
    futures_symbol = config.get("futures_symbol")

    if spot_symbol:
        spot = HyperliquidSpotAdapter(address, info, exchange)
        try:
            s = spot.normalize_symbol(str(spot_symbol))
            meta = spot.get_symbol_meta(s)
            l2 = spot.get_l2(s)
            bids = l2.get("levels", [[]])[0]
            asks = l2.get("levels", [[], []])[1] if len(l2.get("levels", [])) > 1 else []
            if bids and asks:
                bid = decimal.Decimal(str(bids[0]["px"]))
                ask = decimal.Decimal(str(asks[0]["px"]))
                px = bid
                usdc = decimal.Decimal(str(config.get("spot_usdc", 12)))
                qty = _quantize(usdc / px, meta.size_decimals, rounding=decimal.ROUND_DOWN)
                print(f"Spot place BUY {s} px={px} qty={qty}")
                resp = spot.place_order(s, "BUY", qty, px, tif="Gtc")
                oid = None
                if isinstance(resp, dict):
                    try:
                        st = resp["response"]["data"]["statuses"][0]
                        print(f"Spot raw status: {st}")
                        if "resting" in st:
                            oid = st["resting"]["oid"]
                            print(f"Spot oid={oid}")
                    except Exception:
                        print(f"Spot raw resp: {resp}")
                try:
                    oo = info.frontend_open_orders(address)
                    if isinstance(oo, list):
                        print(f"Spot open orders: n={len(oo)}")
                        for o in oo[:3]:
                            print(f"  - coin={o.get('coin')} side={o.get('side')} px={o.get('limitPx')} sz={o.get('sz')} oid={o.get('oid')}")
                except Exception as e:
                    print(f"[Spot] frontend_open_orders failed: {e}")
                if oid is not None:
                    cr = spot.cancel_order(s, oid)
                    print(f"Spot cancel status={cr.get('status')}")
        except Exception as e:
            print(f"[Spot] skipped: {e}")

    if futures_symbol:
        perp = HyperliquidPerpAdapter(address, info, exchange)
        try:
            ps = perp.normalize_symbol(str(futures_symbol))
            meta = perp.get_symbol_meta(ps)
            l2 = perp.get_l2(ps)
            bids = l2.get("levels", [[]])[0]
            asks = l2.get("levels", [[], []])[1] if len(l2.get("levels", [])) > 1 else []
            if bids and asks:
                ask = decimal.Decimal(str(asks[0]["px"]))
                px = ask
                usdc = decimal.Decimal(str(config.get("futures_usdc", 12)))
                qty = _quantize(usdc / px, meta.size_decimals, rounding=decimal.ROUND_DOWN)
                print(f"Perp place SELL {ps} px={px} qty={qty}")
                resp = perp.place_order(ps, "SELL", qty, px, tif="Gtc")
                oid = None
                if isinstance(resp, dict):
                    try:
                        st = resp["response"]["data"]["statuses"][0]
                        print(f"Perp raw status: {st}")
                        if "resting" in st:
                            oid = st["resting"]["oid"]
                            print(f"Perp oid={oid}")
                    except Exception:
                        print(f"Perp raw resp: {resp}")
                try:
                    oo = info.frontend_open_orders(address)
                    if isinstance(oo, list):
                        print(f"Perp open orders: n={len(oo)}")
                        for o in oo[:3]:
                            print(f"  - coin={o.get('coin')} side={o.get('side')} px={o.get('limitPx')} sz={o.get('sz')} oid={o.get('oid')}")
                except Exception as e:
                    print(f"[Perp] frontend_open_orders failed: {e}")
                if oid is not None:
                    cr = perp.cancel_order(ps, oid)
                    print(f"Perp cancel status={cr.get('status')}")
            fd = perp.get_funding(ps)
            print(f"Funding: symbol={fd.get('symbol')} funding={fd.get('funding')} markPx={fd.get('markPx')}")
        except Exception as e:
            print(f"[Perp] skipped: {e}")


# Optional quick entrypoint to run a single step with the new StrategyRunner
if __name__ == "__main__":
    from src.app.runner import main as runner_main
    import sys
    sys.exit(runner_main(["--config", "config.json", "--dry-run", "--once"]))


