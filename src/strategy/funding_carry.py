from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from src.exchanges.base_gateway import ExchangeGateway


@dataclass
class StrategyConfig:
    enter_threshold_apr: Decimal
    exit_threshold_apr: Decimal
    target_usd_notional: Decimal
    hedge_ratio: Decimal = Decimal("1.0")
    price_offset_ticks: int = 1
    tif: str = "Gtc"
    post_only: bool = True


def _best_bid_ask_from_l2(gw: ExchangeGateway, symbol: str) -> Tuple[Decimal, Decimal]:
    l2 = gw.get_l2(symbol)
    levels = l2.get("levels") or []
    bids = levels[0] if len(levels) > 0 else []
    asks = levels[1] if len(levels) > 1 else []
    best_bid = Decimal(str(bids[0]["px"])) if bids else Decimal("0")
    best_ask = Decimal(str(asks[0]["px"])) if asks else Decimal("0")
    return best_bid, best_ask


def _quantize_size(qty: Decimal, size_decimals: int) -> Decimal:
    quantum = Decimal(1).scaleb(-size_decimals)
    return (qty // quantum) * quantum


class FundingCarryStrategy:
    def __init__(
        self,
        spot: ExchangeGateway,
        perp: ExchangeGateway,
        spot_symbol: str,
        perp_symbol: str,
        config: StrategyConfig,
    ) -> None:
        self.spot = spot
        self.perp = perp
        self.spot_symbol = spot_symbol
        self.perp_symbol = perp_symbol
        self.cfg = config

    def compute_expected_funding_apr(self) -> Optional[Decimal]:
        info = self.perp.get_funding(self.perp_symbol)
        raw = info.get("funding")
        if raw is None:
            return None
        try:
            rate = Decimal(str(raw))
        except Exception:
            return None
        # Treat the provided rate as per-window funding; for phase 1 tests, compare directly
        return rate

    def _compute_target_qtys(self) -> Tuple[Decimal, Decimal]:
        spot_meta = self.spot.get_symbol_meta(self.spot_symbol)
        perp_meta = self.perp.get_symbol_meta(self.perp_symbol)
        sbid, sask = _best_bid_ask_from_l2(self.spot, self.spot_symbol)
        pbid, pask = _best_bid_ask_from_l2(self.perp, self.perp_symbol)
        spot_mid = (sbid + sask) / Decimal(2) if sask > 0 else sbid
        perp_mid = (pbid + pask) / Decimal(2) if pask > 0 else pbid
        if spot_mid <= 0 or perp_mid <= 0:
            return Decimal("0"), Decimal("0")
        base_qty = (self.cfg.target_usd_notional / spot_mid) * self.cfg.hedge_ratio
        spot_qty = _quantize_size(base_qty, spot_meta.size_decimals)
        perp_qty = _quantize_size(base_qty, perp_meta.size_decimals)
        return spot_qty, perp_qty

    def _price_for_side(self, gw: ExchangeGateway, symbol: str, side: str, offset_ticks: int) -> Decimal:
        meta = gw.get_symbol_meta(symbol)
        bid, ask = _best_bid_ask_from_l2(gw, symbol)
        if side.upper() == "BUY":
            # passive buy just inside the bid
            price = bid + meta.tick * Decimal(offset_ticks)
            if self.cfg.post_only and ask > 0 and price >= ask:
                price = max(bid, ask - meta.tick)
            return price
        else:
            price = ask - meta.tick * Decimal(offset_ticks)
            if self.cfg.post_only and bid > 0 and price <= bid:
                price = min(ask, bid + meta.tick)
            return price

    def evaluate_and_place(self) -> Dict[str, Any]:
        apr = self.compute_expected_funding_apr()
        result: Dict[str, Any] = {
            "apr": apr,
            "entered": False,
            "orders": [],
            "spot_symbol": self.spot_symbol,
            "perp_symbol": self.perp_symbol,
            "spot_filled_usd": "0",
            "perp_filled_usd": "0",
        }
        if apr is None:
            return result
        if apr >= self.cfg.enter_threshold_apr:
            # Positive funding: long spot, short perp
            spot_qty, perp_qty = self._compute_target_qtys()
            if spot_qty <= 0 or perp_qty <= 0:
                return result

            spot_px = self._price_for_side(self.spot, self.spot_symbol, "BUY", self.cfg.price_offset_ticks)
            perp_px = self._price_for_side(self.perp, self.perp_symbol, "SELL", self.cfg.price_offset_ticks)
            sbid, sask = _best_bid_ask_from_l2(self.spot, self.spot_symbol)
            pbid, pask = _best_bid_ask_from_l2(self.perp, self.perp_symbol)

            # Pre-check: ensure sufficient spot quote balance to buy intended base size
            try:
                quote_ccy = self.spot_symbol.split("/")[1]
                balances = self.spot.get_balances()
                quote_bal = Decimal("0")
                if isinstance(balances, dict) and isinstance(balances.get("balances"), list):
                    for b in balances["balances"]:
                        coin = b.get("coin") or b.get("symbol") or b.get("asset")
                        if str(coin).upper() == quote_ccy.upper():
                            total = b.get("total") or b.get("balance") or b.get("available")
                            quote_bal = Decimal(str(total))
                            break
                needed_quote = (spot_qty * spot_px)
                if quote_bal <= 0 or needed_quote > quote_bal:
                    return result
            except Exception:
                return result

            # Place spot first; if it fails, abort atomic entry
            spot_resp = self.spot.place_order(
                self.spot_symbol,
                "BUY",
                spot_qty,
                spot_px,
                tif=self.cfg.tif,
                reduce_only=False,
                post_only=self.cfg.post_only,
            )
            # Detect error on spot leg
            try:
                statuses = ((spot_resp or {}).get("response") or {}).get("data", {}).get("statuses", [])
                if any(isinstance(s, dict) and "error" in s for s in statuses):
                    result["orders"] = [spot_resp]
                    result["spot_qty"] = str(spot_qty)
                    result["perp_qty"] = str(perp_qty)
                    result["spot_px"] = str(spot_px)
                    result["perp_px"] = str(perp_px)
                    result["spot_best"] = {"bid": str(sbid), "ask": str(sask)}
                    result["perp_best"] = {"bid": str(pbid), "ask": str(pask)}
                    return result
            except Exception:
                return result

            # Place perp leg; if it errors, attempt to cancel spot if resting
            perp_resp = self.perp.place_order(
                self.perp_symbol,
                "SELL",
                perp_qty,
                perp_px,
                tif=self.cfg.tif,
                reduce_only=False,
                post_only=self.cfg.post_only,
            )

            # Parse fills for accurate notional bookkeeping
            def _filled(resp: Any) -> Tuple[Decimal, Decimal, Decimal]:
                try:
                    statuses = ((resp or {}).get("response") or {}).get("data", {}).get("statuses", [])
                    for s in statuses:
                        if isinstance(s, dict) and "filled" in s:
                            f = s["filled"]
                            sz = Decimal(str(f.get("totalSz", "0")))
                            avg = Decimal(str(f.get("avgPx", "0")))
                            return sz * avg, sz, avg
                except Exception:
                    pass
                return Decimal("0"), Decimal("0"), Decimal("0")

            spot_filled_usd, spot_filled_sz, spot_filled_avg = _filled(spot_resp)
            perp_filled_usd, perp_filled_sz, perp_filled_avg = _filled(perp_resp)

            # Check perp errors and try to revert spot if needed
            try:
                perp_statuses = ((perp_resp or {}).get("response") or {}).get("data", {}).get("statuses", [])
                perp_has_error = any(isinstance(s, dict) and "error" in s for s in perp_statuses)
            except Exception:
                perp_has_error = True
            if perp_has_error:
                try:
                    statuses = ((spot_resp or {}).get("response") or {}).get("data", {}).get("statuses", [])
                    for s in statuses:
                        if isinstance(s, dict) and "resting" in s and "oid" in s["resting"]:
                            oid = int(s["resting"]["oid"])
                            try:
                                self.spot.cancel_order(self.spot_symbol, oid)  # type: ignore[attr-defined]
                            except Exception:
                                pass
                            break
                except Exception:
                    pass
                result["orders"] = [spot_resp, perp_resp]
                result["spot_qty"] = str(spot_qty)
                result["perp_qty"] = str(perp_qty)
                result["spot_px"] = str(spot_px)
                result["perp_px"] = str(perp_px)
                result["spot_best"] = {"bid": str(sbid), "ask": str(sask)}
                result["perp_best"] = {"bid": str(pbid), "ask": str(pask)}
                result["spot_filled_usd"] = str(spot_filled_usd)
                result["perp_filled_usd"] = str(perp_filled_usd)
                result["spot_filled_sz"] = str(spot_filled_sz)
                result["perp_filled_sz"] = str(perp_filled_sz)
                result["spot_filled_avg_px"] = str(spot_filled_avg)
                result["perp_filled_avg_px"] = str(perp_filled_avg)
                return result

            result["entered"] = True
            result["orders"] = [spot_resp, perp_resp]
            result["spot_qty"] = str(spot_qty)
            result["perp_qty"] = str(perp_qty)
            result["spot_px"] = str(spot_px)
            result["perp_px"] = str(perp_px)
            result["spot_best"] = {"bid": str(sbid), "ask": str(sask)}
            result["perp_best"] = {"bid": str(pbid), "ask": str(pask)}
            result["spot_filled_usd"] = str(spot_filled_usd)
            result["perp_filled_usd"] = str(perp_filled_usd)
            result["spot_filled_sz"] = str(spot_filled_sz)
            result["perp_filled_sz"] = str(perp_filled_sz)
            result["spot_filled_avg_px"] = str(spot_filled_avg)
            result["perp_filled_avg_px"] = str(perp_filled_avg)
            try:
                ss = ((spot_resp or {}).get("response") or {}).get("data", {}).get("statuses", [])
                for s in ss:
                    if isinstance(s, dict) and "resting" in s and "oid" in s["resting"]:
                        result["spot_oid"] = int(s["resting"]["oid"])
                        break
            except Exception:
                pass
            try:
                ps = ((perp_resp or {}).get("response") or {}).get("data", {}).get("statuses", [])
                for s in ps:
                    if isinstance(s, dict) and "resting" in s and "oid" in s["resting"]:
                        result["perp_oid"] = int(s["resting"]["oid"])
                        break
            except Exception:
                pass
            return result
        # Negative funding path can be added later behind risk toggle
        return result


