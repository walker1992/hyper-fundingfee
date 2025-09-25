from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class DrawdownGuard:
    max_drawdown_usd: Decimal
    _peak: Decimal = Decimal("0")
    _halted: bool = False

    def update_equity(self, equity_usd: Decimal) -> None:
        if equity_usd > self._peak:
            self._peak = equity_usd
        drawdown = self._peak - equity_usd
        if drawdown >= self.max_drawdown_usd:
            self._halted = True

    def halted(self) -> bool:
        return self._halted


