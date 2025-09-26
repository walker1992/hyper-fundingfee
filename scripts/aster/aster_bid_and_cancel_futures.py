import os
import time
import hmac
import hashlib
import urllib.parse
import decimal
import requests
import json

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
except Exception:
    CONFIG = {}

API_KEY = CONFIG.get("api_key")
SECRET_KEY = CONFIG.get("api_secret")

if not API_KEY or not SECRET_KEY:
    print("[ERROR] API credentials missing: set in config.json.")
    exit(1)


BASE_URL = CONFIG.get("futures_base_url") or CONFIG.get("base_url") or "https://fapi.asterdex.com"
API_PREFIX = "/fapi/v1"

TARGET_SYMBOL = CONFIG.get("symbol", "CRVUSDT")
TARGET_USDT_VALUE = decimal.Decimal(str(CONFIG.get("futures_usdt", "5")))
PRICE_OFFSET_TICKS = int(CONFIG.get("price_offset_ticks", "1"))
RECV_WINDOW_MS = int(CONFIG.get("recv_window_ms", "5000"))

DEFAULT_TICK_SIZE = decimal.Decimal(str(CONFIG.get("futures_tick_size", "0.001")))
DEFAULT_STEP_SIZE = decimal.Decimal(str(CONFIG.get("futures_step_size", "1")))

TIME_SYNC_INTERVAL_MS = int(CONFIG.get("time_sync_interval_ms", "60000"))
_SERVER_TIME_OFFSET_MS = 0
_LAST_TIME_SYNC_MS = 0


def _sync_server_time():
    global _SERVER_TIME_OFFSET_MS, _LAST_TIME_SYNC_MS
    try:
        resp = requests.get(f"{BASE_URL}{API_PREFIX}/time")
        resp.raise_for_status()
        server_ms = int(resp.json().get("serverTime", 0))
        local_ms = int(time.time() * 1000)
        _SERVER_TIME_OFFSET_MS = server_ms - local_ms
        _LAST_TIME_SYNC_MS = local_ms
    except Exception:
        pass


def _now_ms() -> int:
    return int(time.time() * 1000) + _SERVER_TIME_OFFSET_MS


def generate_signature(params_str: str) -> str:
    return hmac.new(SECRET_KEY.encode("utf-8"), params_str.encode("utf-8"), hashlib.sha256).hexdigest()


def make_signed_request(method: str, endpoint: str, params: dict | None = None):
    if params is None:
        params = {}

    if _LAST_TIME_SYNC_MS == 0 or (int(time.time() * 1000) - _LAST_TIME_SYNC_MS) > TIME_SYNC_INTERVAL_MS:
        _sync_server_time()
    params_for_signing = dict(params)
    params_for_signing["timestamp"] = _now_ms()
    params_for_signing["recvWindow"] = RECV_WINDOW_MS

    # Sorted query to sign
    query_string_to_sign = urllib.parse.urlencode(sorted(params_for_signing.items()))
    signature = generate_signature(query_string_to_sign)

    # Build final URL with signature in query string
    final_query_string = f"{query_string_to_sign}&signature={signature}"
    full_url = f"{BASE_URL}{endpoint}?{final_query_string}"

    headers = {"X-MBX-APIKEY": API_KEY}
    if method.upper() == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if method.upper() == "DELETE":
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    if method.upper() == "GET":
        resp = requests.get(full_url, headers=headers)
    elif method.upper() == "POST":
        # Send signed params in the request body per docs; keep signature at end
        post_url = f"{BASE_URL}{endpoint}"
        resp = requests.post(post_url, headers=headers, data=final_query_string)
    elif method.upper() == "DELETE":
        resp = requests.delete(full_url, headers=headers)
    else:
        raise ValueError(f"Unsupported method: {method}")

    if not resp.ok:
        try:
            err_json = resp.json()
            code = err_json.get("code")
            msg = err_json.get("msg")
            if code in (-1021, "-1021"):
                _sync_server_time()
                params_for_signing["timestamp"] = _now_ms()
                query_string_to_sign_retry = urllib.parse.urlencode(sorted(params_for_signing.items()))
                signature_retry = generate_signature(query_string_to_sign_retry)
                final_query_string_retry = f"{query_string_to_sign_retry}&signature={signature_retry}"
                if method.upper() == "GET":
                    resp = requests.get(f"{BASE_URL}{endpoint}?{final_query_string_retry}", headers=headers)
                elif method.upper() == "POST":
                    resp = requests.post(f"{BASE_URL}{endpoint}", headers=headers, data=final_query_string_retry)
                elif method.upper() == "DELETE":
                    resp = requests.delete(f"{BASE_URL}{endpoint}?{final_query_string_retry}", headers=headers)
                if resp.ok:
                    return resp.json()
                try:
                    err_json = resp.json()
                    code = err_json.get("code")
                    msg = err_json.get("msg")
                except ValueError:
                    pass
            raise requests.HTTPError(f"HTTP {resp.status_code}: code={code} msg={msg}", response=resp)
        except ValueError:
            raise requests.HTTPError(f"HTTP {resp.status_code}: {resp.text}", response=resp)

    return resp.json()


def get_book_ticker(symbol: str) -> dict | None:
    """Fetch best bid/ask for a symbol."""
    endpoint = f"{API_PREFIX}/ticker/bookTicker"
    try:
        resp = requests.get(f"{BASE_URL}{endpoint}", params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
        return {
            "bidPrice": decimal.Decimal(data["bidPrice"]),
            "askPrice": decimal.Decimal(data["askPrice"]),
        }
    except requests.RequestException as e:
        print(f"[ERROR] get_book_ticker failed: {e}")
        return None


def fetch_symbol_filters(symbol: str) -> tuple[decimal.Decimal, decimal.Decimal, decimal.Decimal | None]:
    """Fetch tickSize (price), stepSize (qty), and optional minNotional from exchangeInfo."""
    endpoint = f"{API_PREFIX}/exchangeInfo"
    try:
        resp = requests.get(f"{BASE_URL}{endpoint}")
        resp.raise_for_status()
        info = resp.json()
        symbols = info.get("symbols", [])
        for s in symbols:
            if s.get("symbol") == symbol:
                tick_size = DEFAULT_TICK_SIZE
                step_size = DEFAULT_STEP_SIZE
                min_notional: decimal.Decimal | None = None
                for f in s.get("filters", []):
                    ftype = f.get("filterType")
                    if ftype == "PRICE_FILTER":
                        tick_size = decimal.Decimal(str(f.get("tickSize", DEFAULT_TICK_SIZE)))
                    elif ftype == "LOT_SIZE":
                        step_size = decimal.Decimal(str(f.get("stepSize", DEFAULT_STEP_SIZE)))
                    elif ftype in ("MIN_NOTIONAL", "NOTIONAL"):
                        v = f.get("minNotional") or f.get("minNotionalValue") or f.get("minNotionalBase")
                        if v is not None:
                            min_notional = decimal.Decimal(str(v))
                return tick_size, step_size, min_notional
    except requests.RequestException as e:
        print(f"[WARN] fetch_symbol_filters failed, fallback to defaults: {e}")
    return DEFAULT_TICK_SIZE, DEFAULT_STEP_SIZE, None


def quantize_by_step(value: decimal.Decimal, step: decimal.Decimal, rounding=decimal.ROUND_DOWN) -> decimal.Decimal:
    """Quantize value to the step's decimal places."""
    exp = decimal.Decimal(str(step)).normalize()
    # Determine quantize exponent like Decimal('0.001') -> Decimal('0.001')
    return value.quantize(exp, rounding=rounding)


def place_limit_order(symbol: str, side: str, usdt_amount: decimal.Decimal, price_offset_ticks: int, base_ticker: dict | None = None):
    ticker = base_ticker if base_ticker else get_book_ticker(symbol)
    if not ticker:
        print("[ERROR] No ticker data; cannot place order.")
        return None

    tick_size, step_size, min_notional = fetch_symbol_filters(symbol)

    bid = ticker["bidPrice"]
    ask = ticker["askPrice"]

    # Compute target price with offset ticks
    if side.upper() == "BUY":
        target_price = bid + decimal.Decimal(price_offset_ticks) * tick_size
        if target_price >= ask:
            target_price = bid
    elif side.upper() == "SELL":
        target_price = ask - decimal.Decimal(price_offset_ticks) * tick_size
        if target_price <= bid:
            target_price = ask
    else:
        print(f"[ERROR] Invalid side: {side}")
        return None

    if target_price <= 0:
        print("[ERROR] Invalid target price.")
        return None

    # Quantize price to tick size
    q_price = quantize_by_step(target_price, tick_size, rounding=decimal.ROUND_HALF_UP)

    # Calculate quantity by notional / price
    quantity_unrounded = (usdt_amount / q_price)
    q_qty = quantize_by_step(quantity_unrounded, step_size, rounding=decimal.ROUND_DOWN)

    if q_qty <= 0:
        print("[ERROR] Quantity too small after rounding.")
        return None

    notional = q_price * q_qty
    if min_notional is not None and notional < min_notional:
        print(f"[WARN] Notional {notional} < minNotional {min_notional}; order may be rejected.")

    endpoint = f"{API_PREFIX}/order"
    params = {
        "symbol": symbol,
        "side": side.upper(),
        "type": "LIMIT",
        "timeInForce": "GTC",
        "quantity": str(q_qty),
        "price": str(q_price),
    }
    return make_signed_request("POST", endpoint, params)


def cancel_order(symbol: str, order_id: int | str):
    endpoint = f"{API_PREFIX}/order"
    params = {"symbol": symbol, "orderId": str(order_id)}
    return make_signed_request("DELETE", endpoint, params)


def get_open_orders(symbol: str | None = None):
    endpoint = f"{API_PREFIX}/openOrders"
    params = {"symbol": symbol} if symbol else {}
    return make_signed_request("GET", endpoint, params)


def cancel_with_retry(symbol: str, order_id: int | str | None = None, client_order_id: str | None = None, max_retries: int = 3, backoff_seconds: float = 0.35):
    attempt = 0
    last_error = None
    while attempt < max_retries:
        try:
            endpoint = f"{API_PREFIX}/order"
            params: dict = {"symbol": symbol}
            if order_id is not None:
                params["orderId"] = str(order_id)
            elif client_order_id is not None:
                params["origClientOrderId"] = client_order_id
            else:
                raise ValueError("Either order_id or client_order_id must be provided")
            return make_signed_request("DELETE", endpoint, params)
        except requests.HTTPError as e:
            last_error = e
            # If unknown order, verify current open orders and possibly retry
            try:
                oo = get_open_orders(symbol)
                still_open = False
                if isinstance(oo, list):
                    for o in oo:
                        if order_id is not None and str(o.get("orderId")) == str(order_id):
                            still_open = True
                            break
                        if client_order_id is not None and o.get("clientOrderId") == client_order_id:
                            still_open = True
                            break
                if not still_open:
                    # Treat as already canceled/filled
                    return {"status": "CANCELED", "orderId": order_id, "origClientOrderId": client_order_id}
            except Exception:
                pass
            time.sleep(backoff_seconds)
            attempt += 1
            # swap identifier on next attempt
            if attempt == 1 and client_order_id is not None and order_id is not None:
                pass
    if last_error:
        raise last_error
    raise RuntimeError("Cancel failed without explicit error")


def print_futures_balances(symbol: str):
    try:
        # Futures account info endpoint (USER_DATA)
        info = make_signed_request("GET", f"{API_PREFIX}/account", {})
        print("Futures Balances:")
        if isinstance(info, dict):
            # Try common fields seen in futures-style accounts
            assets = info.get("assets") or info.get("balances") or []
            # Print top-level margin and USDT-like balances
            if isinstance(assets, list) and assets:
                for a in assets:
                    asset = a.get("asset") or a.get("currency")
                    if asset in ("USDT", "USD", "ASTER", "ASTERUSDT"):
                        free = a.get("walletBalance") or a.get("availableBalance") or a.get("crossWalletBalance") or a.get("balance") or a.get("free")
                        locked = a.get("crossUnPnl") or a.get("locked") or a.get("initialMargin")
                        print(f"  - {asset}: free={free} locked={locked}")
            else:
                print(f"  raw: {info}")
        else:
            print(f"  raw: {info}")
    except Exception as e:
        print(f"[WARN] print_futures_balances failed: {e}")


def verify_credentials() -> bool:
    endpoint = f"{API_PREFIX}/account"
    try:
        r = make_signed_request("GET", endpoint, {})
        return isinstance(r, dict)
    except Exception as e:
        print(f"[ERROR] Credential verification failed: {e}")
        return False


if __name__ == "__main__":
    print(f"Starting futures bid-and-cancel for {TARGET_SYMBOL}...")

    if not verify_credentials():
        print("[ERROR] Unauthorized: please check API key/secret permissions and IP whitelist in config.json")
        exit(1)

    # Print balances once at start
    print_futures_balances(TARGET_SYMBOL)

    iterations = int(CONFIG.get("iterations", "2"))
    delay_seconds = float(CONFIG.get("delay_seconds", "10"))
    monitor_orders = bool(CONFIG.get("monitor_orders", True))

    for i in range(iterations):
        print(f"--- Iteration {i + 1}/{iterations} ---")
        # Print balances each iteration to diagnose -2018
        print_futures_balances(TARGET_SYMBOL)

        ticker = get_book_ticker(TARGET_SYMBOL)
        if not ticker:
            print("[WARN] Could not fetch ticker; retry next iteration.")
            time.sleep(delay_seconds)
            continue
        print(f"Ticker: bid={ticker['bidPrice']} ask={ticker['askPrice']}")

        buy_order_id = None
        sell_order_id = None
        buy_client_order_id = None
        sell_client_order_id = None

        # Place BUY limit
        buy_resp = place_limit_order(
            symbol=TARGET_SYMBOL,
            side="BUY",
            usdt_amount=TARGET_USDT_VALUE,
            price_offset_ticks=PRICE_OFFSET_TICKS,
            base_ticker=ticker,
        )
        if buy_resp and isinstance(buy_resp, dict) and buy_resp.get("orderId") is not None:
            buy_order_id = buy_resp["orderId"]
            buy_client_order_id = buy_resp.get("clientOrderId")
            print(f"BUY placed: id={buy_order_id} status={buy_resp.get('status')}")
        else:
            print(f"BUY placement failed: {buy_resp}")

        # Place SELL limit
        sell_resp = place_limit_order(
            symbol=TARGET_SYMBOL,
            side="SELL",
            usdt_amount=TARGET_USDT_VALUE,
            price_offset_ticks=PRICE_OFFSET_TICKS,
            base_ticker=ticker,
        )
        if sell_resp and isinstance(sell_resp, dict) and sell_resp.get("orderId") is not None:
            sell_order_id = sell_resp["orderId"]
            sell_client_order_id = sell_resp.get("clientOrderId")
            print(f"SELL placed: id={sell_order_id} status={sell_resp.get('status')}")
        else:
            print(f"SELL placement failed: {sell_resp}")

        if monitor_orders:
            try:
                oo = get_open_orders(TARGET_SYMBOL)
                if isinstance(oo, list) and oo:
                    print("Open Orders:")
                    for o in oo:
                        print(f"  - id={o.get('orderId')} side={o.get('side')} price={o.get('price')} qty={o.get('origQty')} status={o.get('status')}")
                else:
                    print("Open Orders: (None)")
            except Exception as e:
                print(f"[WARN] get_open_orders failed: {e}")

        time.sleep(0.5)

        # Cancel the two orders if created
        if buy_order_id is not None or buy_client_order_id is not None:
            try:
                cbr = cancel_with_retry(TARGET_SYMBOL, order_id=buy_order_id, client_order_id=buy_client_order_id)
                print(f"BUY cancel resp: {cbr}")
            except Exception as e:
                print(f"[WARN] Cancel BUY failed: {e}")
        if sell_order_id is not None or sell_client_order_id is not None:
            try:
                csr = cancel_with_retry(TARGET_SYMBOL, order_id=sell_order_id, client_order_id=sell_client_order_id)
                print(f"SELL cancel resp: {csr}")
            except Exception as e:
                print(f"[WARN] Cancel SELL failed: {e}")

        print(f"--- End Iteration {i + 1}/{iterations} ---")
        time.sleep(delay_seconds)

    print("Futures bid-and-cancel finished.")
