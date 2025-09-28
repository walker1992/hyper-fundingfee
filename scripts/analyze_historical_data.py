import argparse
import os
import sys
import time
import requests
from datetime import datetime


def _load_api_key(explicit_key: str | None) -> str | None:
    if explicit_key:
        return explicit_key
    env_key = os.environ.get("COINALYZE_API_KEY")
    if env_key:
        return env_key
    cfg_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "config.json"))
    try:
        import json
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return (
            (cfg.get("coinalyze") or {}).get("api_key")
            or (cfg.get("credentials") or {}).get("coinalyze_api_key")
        )
    except Exception:
        return None


def analyze_historical_data(token: str, days: int = 30, api_key: str | None = None) -> dict | None:
    try:
        end_time = int(time.time())
        start_time = end_time - (days * 24 * 60 * 60)

        symbols = [f"{token}USDT.6", f"{token}.H"]
        interval = "daily" # "1hour"

        api_key = _load_api_key(api_key)
        if not api_key:
            print("⚠️ Missing Coinalyze API key. Provide --api-key or set COINALYZE_API_KEY.")
            return None

        base_url = "https://api.coinalyze.net/v1/funding-rate-history"
        params = {
            "symbols": ",".join(symbols),
            "interval": interval,
            "from": start_time,
            "to": end_time,
            "api_key": api_key,
        }

        response = requests.get(base_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        print(f"Coinalyze funding-rate-history: {data}")
        
        funding_by_ts: dict[int, dict] = {}
        for entry in data:
            symbol = entry.get("symbol", "Unknown")
            exchange = "Bybit" if symbol.endswith(".6") else "Hyperliquid"
            history = entry.get("history", [])
            for rec in history:
                ts = int(rec.get("t"))
                rate = float(rec.get("c"))
                if exchange == "Bybit":
                    rate = rate / 8 if 8 else rate
                if ts not in funding_by_ts:
                    funding_by_ts[ts] = {}
                funding_by_ts[ts][exchange] = {
                    "symbol": symbol,
                    "funding_rate": rate,
                }

        rows = []
        for ts, ex in sorted(funding_by_ts.items()):
            if "Bybit" in ex and "Hyperliquid" in ex:
                bybit_rate = float(ex["Bybit"]["funding_rate"])
                hl_rate = float(ex["Hyperliquid"]["funding_rate"])
                long_side = "Bybit" if bybit_rate < hl_rate else "Hyperliquid"
                arb_rate = abs(bybit_rate - hl_rate)
                rows.append(
                    {
                        "ts": ts,
                        "date": datetime.utcfromtimestamp(ts),
                        "bybit_symbol": ex["Bybit"]["symbol"],
                        "hyper_symbol": ex["Hyperliquid"]["symbol"],
                        "bybit_rate": bybit_rate,
                        "hyper_rate": hl_rate,
                        "long": long_side,
                        "arb": arb_rate,
                    }
                )

        if not rows:
            return None

        HOURS_7D = 168
        HOURS_30D = 720

        def _calc_period_stats(slice_rows: list[dict]) -> dict:
            slice_rows = [r for r in slice_rows if r["arb"] > 0]
            if not slice_rows:
                return {
                    "bybit_success": 0.0,
                    "better_side": "None",
                    "better_apr": 0.0,
                    "max_arb": 0.0,
                    "min_arb": 0.0,
                    "zero_rate_pct": 100.0,
                }

            total = len(slice_rows)
            bybit_long = sum(1 for r in slice_rows if r["long"] == "Bybit")
            bybit_success = (bybit_long / total) * 100 if total else 0.0

            bybit_total = sum((r["hyper_rate"] - r["bybit_rate"]) for r in slice_rows if r["long"] == "Bybit")
            hl_total = sum((r["bybit_rate"] - r["hyper_rate"]) for r in slice_rows if r["long"] == "Hyperliquid")

            period_days = min(7.0, len(slice_rows) / 24.0)
            if len(slice_rows) > HOURS_7D:
                period_days = min(30.0, len(slice_rows) / 24.0)

            bybit_apr = (bybit_total / period_days) * 365 if period_days > 0 else 0.0
            hl_apr = (hl_total / period_days) * 365 if period_days > 0 else 0.0

            better_side = "Bybit" if bybit_apr > hl_apr else "Hyperliquid"
            better_apr = max(bybit_apr, hl_apr)

            if better_side == "Bybit":
                arb_rates = [(r["hyper_rate"] - r["bybit_rate"]) for r in slice_rows]
            else:
                arb_rates = [(r["bybit_rate"] - r["hyper_rate"]) for r in slice_rows]

            max_arb = max(arb_rates) if arb_rates else 0.0
            min_arb = min(arb_rates) if arb_rates else 0.0

            total_hours = HOURS_7D if len(slice_rows) <= HOURS_7D else HOURS_30D
            zero_rate_pct = ((total_hours - len(slice_rows)) / total_hours) * 100.0

            return {
                "bybit_success": bybit_success,
                "better_side": better_side,
                "better_apr": better_apr,
                "max_arb": max_arb,
                "min_arb": min_arb,
                "zero_rate_pct": zero_rate_pct,
            }

        head_7d = rows[:HOURS_7D]
        head_30d = rows[:HOURS_30D]
        stats_7d = _calc_period_stats(head_7d)
        stats_30d = _calc_period_stats(head_30d)

        last = rows[-1]
        current_bybit_rate = float(last["bybit_rate"])
        current_hl_rate = float(last["hyper_rate"])

        return {
            "success_rate_7d": stats_7d["bybit_success"],
            "better_side_7d": stats_7d["better_side"],
            "apr_7d": stats_7d["better_apr"],
            "success_rate_30d": stats_30d["bybit_success"],
            "better_side_30d": stats_30d["better_side"],
            "apr_30d": stats_30d["better_apr"],
            "max_arb_7d": stats_7d["max_arb"],
            "min_arb_7d": stats_7d["min_arb"],
            "max_arb_30d": stats_30d["max_arb"],
            "min_arb_30d": stats_30d["min_arb"],
            "current_bybit_rate": current_bybit_rate,
            "current_hl_rate": current_hl_rate,
            "zero_rate_pct_7d": stats_7d["zero_rate_pct"],
        }
    except Exception as e:
        print(f"⚠️ Error analyzing historical data: {e}")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze historical funding-rate arbitrage between Bybit and Hyperliquid")
    parser.add_argument("token", help="Base token symbol, e.g., BTC")
    parser.add_argument("--days", type=int, default=30, help="Number of days to analyze (default: 30)")
    parser.add_argument("--api-key", dest="api_key", default=None, help="Coinalyze API key (or set COINALYZE_API_KEY env)")
    args = parser.parse_args(argv)

    token = args.token.strip().upper()
    res = analyze_historical_data(token, days=args.days, api_key=args.api_key)
    if not res:
        print("No analysis available.")
        return 1

    current_long = "Bybit" if float(res["current_bybit_rate"]) < float(res["current_hl_rate"]) else "Hyperliquid"
    current_apr = abs(float(res["current_bybit_rate"]) - float(res["current_hl_rate"])) * 24 * 365

    print("\n📊 Historical Analysis Summary")
    print("=" * 80)
    print(f"Token: {token}")
    print(f"7D Success (Bybit long): {res['success_rate_7d']:.2f}% | 30D: {res['success_rate_30d']:.2f}%")
    print(f"7D Best Side: {res['better_side_7d']} APR: {res['apr_7d']:.4f}% | 30D Best Side: {res['better_side_30d']} APR: {res['apr_30d']:.4f}%")
    print(f"7D Max/Min: {res['max_arb_7d']:.6f}/{res['min_arb_7d']:.6f} | 30D Max/Min: {res['max_arb_30d']:.6f}/{res['min_arb_30d']:.6f}")
    print(f"Current Long: {current_long} (APR ~ {current_apr:.4f}%)")
    print(f"Zero-rate hours (7D window) missing%: {res['zero_rate_pct_7d']:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())


