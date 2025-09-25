from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

try:  # Allow tests to run without the SDK installed
    from hyperliquid.info import Info  # type: ignore
except Exception:  # pragma: no cover - fallback for environments without SDK
    Info = Any  # type: ignore


@dataclass
class HLClients:
    address: str
    info: Info
    # exchange object is provided by caller; we keep it as Any to avoid typing imports here
    exchange: Any


def infer_tick_from_l2(info: Info, symbol: str) -> Optional[Decimal]:
    try:
        l2 = info.l2_snapshot(symbol)
        levels = l2.get("levels") or []
        bids = levels[0] if len(levels) > 0 else []
        asks = levels[1] if len(levels) > 1 else []
        pxs = []
        for side in (bids[:10], asks[:10]):
            pxs.extend([Decimal(str(l["px"])) for l in side])
        pxs = sorted(set(pxs), reverse=True)
        diffs = [abs(pxs[i] - pxs[i + 1]) for i in range(len(pxs) - 1)]
        diffs = [d for d in diffs if d > 0]
        if diffs:
            return min(diffs)
    except Exception:
        return None
    return None


def best_bid_ask(info: Info, symbol: str) -> Tuple[Decimal, Decimal]:
    l2 = info.l2_snapshot(symbol)
    levels = l2.get("levels") or []
    bids = levels[0] if len(levels) > 0 else []
    asks = levels[1] if len(levels) > 1 else []
    best_bid = Decimal(str(bids[0]["px"])) if bids else Decimal("0")
    best_ask = Decimal(str(asks[0]["px"])) if asks else Decimal("0")
    return best_bid, best_ask


