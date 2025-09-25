from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class RebalanceDecision:
    spot_delta: Decimal
    perp_delta: Decimal


def decide_rebalance(current_spot: Decimal, current_perp: Decimal, target_base: Decimal, tolerance: Decimal = Decimal("0.0")) -> Optional[RebalanceDecision]:
    spot_needed = target_base - current_spot
    perp_needed = target_base - current_perp
    if abs(spot_needed) <= tolerance and abs(perp_needed) <= tolerance:
        return None
    return RebalanceDecision(spot_delta=spot_needed, perp_delta=perp_needed)


