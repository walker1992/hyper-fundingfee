from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class SlippageController:
    max_bps: int

    def enforce(self, reference_px: Decimal, proposed_px: Decimal, is_buy: bool) -> Decimal:
        if reference_px <= 0:
            return proposed_px
        max_move = (reference_px * Decimal(self.max_bps)) / Decimal(10000)
        if is_buy:
            # cap above reference
            return min(proposed_px, reference_px + max_move)
        else:
            # cap below reference
            return max(proposed_px, reference_px - max_move)


