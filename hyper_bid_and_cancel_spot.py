import os
import json
import time
import decimal

import eth_account
from eth_account.signers.local import LocalAccount

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
except Exception:
    CONFIG = {}


def _load_wallet_from_config(config: dict) -> tuple[str, LocalAccount]:
    secret_key = config.get("secret_key")
    if not secret_key:
        raise RuntimeError("secret_key missing in config.json for Hyperliquid API wallet")
    account: LocalAccount = eth_account.Account.from_key(secret_key)
    address = config.get("account_address") or account.address
    return address, account


def _setup_clients(base_url: str | None, skip_ws: bool = True) -> tuple[str, Info, Exchange]:
    address, account = _load_wallet_from_config(CONFIG)
    info = Info(base_url or constants.TESTNET_API_URL, skip_ws=skip_ws)
    exchange = Exchange(account, base_url or constants.TESTNET_API_URL, account_address=address)
    return address, info, exchange


def _quantize(value: decimal.Decimal, decimals: int, rounding=decimal.ROUND_DOWN) -> decimal.Decimal:
    exp = decimal.Decimal(1).scaleb(-decimals)
    return value.quantize(exp, rounding=rounding)


def _get_bid_ask(info: Info, symbol: str) -> tuple[decimal.Decimal, decimal.Decimal]:
    l2 = info.l2_snapshot(symbol)
    levels = l2.get("levels") or []
    bids = levels[0] if len(levels) > 0 else []
    asks = levels[1] if len(levels) > 1 else []
    best_bid = decimal.Decimal(bids[0]["px"]) if bids else decimal.Decimal("0")
    best_ask = decimal.Decimal(asks[0]["px"]) if asks else decimal.Decimal("0")
    return best_bid, best_ask


def _infer_price_tick_from_l2(info: Info, symbol: str) -> decimal.Decimal | None:
    try:
        l2 = info.l2_snapshot(symbol)
        levels = l2.get("levels") or []
        bids = levels[0] if len(levels) > 0 else []
        asks = levels[1] if len(levels) > 1 else []

        def collect_diffs(side):
            pxs = [decimal.Decimal(l["px"]) for l in side[:10]]
            pxs = sorted(set(pxs), reverse=True)
            diffs = []
            for i in range(len(pxs) - 1):
                d = abs(pxs[i] - pxs[i + 1])
                if d > 0:
                    diffs.append(d)
            return diffs

        diffs = collect_diffs(bids) + collect_diffs(asks)
        if diffs:
            return min(diffs)
    except Exception:
        pass
    return None


def place_limit_order(info: Info, exchange: Exchange, symbol: str, side: str, usdc_amount: decimal.Decimal, price_offset_ticks: int):
    # Use SDK's name->asset mapping to derive base size precision for this spot pair
    asset = info.name_to_asset(symbol)
    base_sz_decimals = int(info.asset_to_sz_decimals[asset])
    # Prefer real tick from L2; fallback to decimals rule (8 - szDecimals)
    tick = _infer_price_tick_from_l2(info, symbol)
    if tick is None:
        price_decimals = 8 - base_sz_decimals
        tick = decimal.Decimal(1).scaleb(-price_decimals)

    bid, ask = _get_bid_ask(info, symbol)
    if bid <= 0 or ask <= 0:
        return None

    if side.upper() == "BUY":
        if tick is None or tick <= 0:
            q_price = bid
        else:
            target_price = bid + decimal.Decimal(price_offset_ticks) * tick
            if target_price >= ask:
                target_price = bid
            q_price = (decimal.Decimal(target_price) / tick).to_integral_value(rounding=decimal.ROUND_HALF_UP) * tick
    elif side.upper() == "SELL":
        if tick is None or tick <= 0:
            q_price = ask
        else:
            target_price = ask - decimal.Decimal(price_offset_ticks) * tick
            if target_price <= bid:
                target_price = ask
            q_price = (decimal.Decimal(target_price) / tick).to_integral_value(rounding=decimal.ROUND_HALF_UP) * tick
    else:
        return None
    quantity_unrounded = usdc_amount / q_price
    q_qty = _quantize(quantity_unrounded, base_sz_decimals, rounding=decimal.ROUND_DOWN)
    if q_qty <= 0:
        return None

    is_buy = side.upper() == "BUY"
    order_type = {"limit": {"tif": "Gtc"}}
    print(f"Order params: side={'BUY' if is_buy else 'SELL'} symbol={symbol} price={q_price} qty={q_qty} tif=Gtc")
    return exchange.order(symbol, is_buy, float(q_qty), float(q_price), order_type)


def _normalize_spot_symbol(info: Info, raw_symbol: str) -> str:
    # Accept formats like "BASE/QUOTE" or compact like "BASEUSDT"
    candidate = raw_symbol.strip()
    if "/" in candidate:
        if candidate in info.name_to_coin:
            return candidate
    else:
        for quote in ("USDC", "USDT", "USD"):
            if candidate.endswith(quote) and len(candidate) > len(quote):
                base = candidate[: -len(quote)]
                pair = f"{base}/{quote}"
                if pair in info.name_to_coin:
                    return pair
    # Fallback: if already a valid name like "@8"
    if candidate in info.name_to_coin:
        return candidate
    raise ValueError(candidate)


def _print_spot_pairs(info: Info, limit: int = 50):
    spot_meta = info.spot_meta()
    tokens = spot_meta["tokens"]
    token_names = {i: t["name"] for i, t in enumerate(tokens)}
    pairs = []
    for s in spot_meta["universe"]:
        base_i, quote_i = s["tokens"]
        base = tokens[base_i]["name"]
        quote = tokens[quote_i]["name"]
        pairs.append(f"{base}/{quote}")
    pairs = sorted(set(pairs))
    print(f"Available spot pairs (showing up to {limit}/{len(pairs)}):")
    for p in pairs[:limit]:
        print(f"  - {p}")


def _print_spot_balances(info: Info, address: str):
    try:
        state = info.spot_user_state(address)
        balances = state.get("balances", []) if isinstance(state, dict) else []
        print("Spot Balances:")
        if not balances:
            print("  (none)")
        for b in balances:
            print(json.dumps(b))
    except Exception as e:
        print(f"[WARN] fetch spot balances failed: {e}")


def get_open_orders(info: Info, address: str):
    return info.frontend_open_orders(address)


def cancel_order(exchange: Exchange, symbol: str, oid: int):
    print(f"Cancel params: symbol={symbol} oid={oid}")
    return exchange.cancel(symbol, oid)


def main():
    print("Starting Hyperliquid spot bid-and-cancel...")

    base_url = CONFIG.get("base_url")  # optional; defaults to TESTNET when None
    raw_symbol = CONFIG.get("spot_symbol") or "HYPE"
    price_offset_ticks = int(CONFIG.get("price_offset_ticks", 1))
    usdc_value = decimal.Decimal(str(CONFIG.get("spot_usdc", 12)))
    iterations = int(CONFIG.get("iterations", 1))
    delay_seconds = float(CONFIG.get("delay_seconds", 2))
    monitor_orders = bool(CONFIG.get("monitor_orders", True))

    address, info, exchange = _setup_clients(base_url, skip_ws=True)

    # Print balances once at start
    _print_spot_balances(info, address)

    # Normalize and validate symbol
    try:
        symbol = _normalize_spot_symbol(info, raw_symbol)
    except ValueError:
        print(f"[ERROR] Unknown spot symbol: {raw_symbol}")
        _print_spot_pairs(info)
        return

    for i in range(iterations):
        print(f"--- Iteration {i + 1}/{iterations} ---")

        # Print balances each loop for easier debugging
        _print_spot_balances(info, address)

        # Debug tick and quotes (concise)
        bid, ask = _get_bid_ask(info, symbol)
        tick_dbg = _infer_price_tick_from_l2(info, symbol)
        print(f"Debug: bid={bid} ask={ask} tick={tick_dbg}")

        buy_resp = place_limit_order(info, exchange, symbol, "BUY", usdc_value, price_offset_ticks)
        sell_resp = place_limit_order(info, exchange, symbol, "SELL", usdc_value, price_offset_ticks)

        buy_oid = None
        sell_oid = None
        if isinstance(buy_resp, dict) and buy_resp.get("status") == "ok":
            st = buy_resp["response"]["data"]["statuses"][0]
            if "resting" in st:
                buy_oid = st["resting"]["oid"]
                print(f"BUY result: oid={buy_oid}")
            elif "error" in st:
                print(f"BUY error: {st['error']}")
            else:
                print(f"BUY result: {st}")
        else:
            print(f"BUY error: {buy_resp}")

        if isinstance(sell_resp, dict) and sell_resp.get("status") == "ok":
            st = sell_resp["response"]["data"]["statuses"][0]
            if "resting" in st:
                sell_oid = st["resting"]["oid"]
                print(f"SELL result: oid={sell_oid}")
            elif "error" in st:
                print(f"SELL error: {st['error']}")
            else:
                print(f"SELL result: {st}")
        else:
            print(f"SELL error: {sell_resp}")

        if monitor_orders:
            try:
                oo = get_open_orders(info, address)
                if isinstance(oo, list) and oo:
                    print(f"Open Orders: n={len(oo)}")
                    for o in oo[:2]:
                        print(f"  - coin={o.get('coin')} side={o.get('side')} px={o.get('limitPx')} sz={o.get('sz')} oid={o.get('oid')}")
                else:
                    print("Open Orders: (None)")
            except Exception as e:
                print(f"[WARN] get_open_orders failed: {e}")

        time.sleep(0.5)

        if buy_oid is not None:
            try:
                cbr = cancel_order(exchange, symbol, buy_oid)
                if isinstance(cbr, dict) and cbr.get("status") == "ok":
                    st = cbr["response"]["data"]["statuses"][0]
                    print(f"BUY cancel: {st}")
                else:
                    print(f"BUY cancel error: {cbr}")
            except Exception as e:
                print(f"[WARN] Cancel BUY failed: {e}")
        if sell_oid is not None:
            try:
                csr = cancel_order(exchange, symbol, sell_oid)
                if isinstance(csr, dict) and csr.get("status") == "ok":
                    st = csr["response"]["data"]["statuses"][0]
                    print(f"SELL cancel: {st}")
                else:
                    print(f"SELL cancel error: {csr}")
            except Exception as e:
                print(f"[WARN] Cancel SELL failed: {e}")

        print(f"--- End Iteration {i + 1}/{iterations} ---")
        time.sleep(delay_seconds)

    print("Hyperliquid spot bid-and-cancel finished.")


if __name__ == "__main__":
    main()


