from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict


@dataclass
class NotionalLimiter:
    per_symbol_cap: Decimal
    portfolio_cap: Decimal
    symbol_to_notional: Dict[str, Decimal] = field(default_factory=dict)

    def can_add(self, symbol: str, delta_usd: Decimal) -> bool:
        cur_symbol = self.symbol_to_notional.get(symbol, Decimal("0"))
        new_symbol = cur_symbol + delta_usd
        if new_symbol < 0:
            new_symbol = Decimal("0")
        portfolio_total = sum(self.symbol_to_notional.values()) + delta_usd
        if portfolio_total < 0:
            portfolio_total = Decimal("0")
        return new_symbol <= self.per_symbol_cap and portfolio_total <= self.portfolio_cap

    def apply(self, symbol: str, delta_usd: Decimal) -> None:
        if not self.can_add(symbol, delta_usd):
            raise ValueError("Notional cap exceeded")
        self.symbol_to_notional[symbol] = self.symbol_to_notional.get(symbol, Decimal("0")) + delta_usd
        if self.symbol_to_notional[symbol] < 0:
            self.symbol_to_notional[symbol] = Decimal("0")


@dataclass
class OrderRateLimiter:
    max_actions_per_min: int
    _times: list[float] = field(default_factory=list)

    def allow(self) -> bool:
        now = time.time()
        one_min_ago = now - 60
        self._times = [t for t in self._times if t >= one_min_ago]
        if len(self._times) < self.max_actions_per_min:
            self._times.append(now)
            return True
        return False


