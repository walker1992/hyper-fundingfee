from decimal import Decimal
import time

from src.risk.limits import NotionalLimiter, OrderRateLimiter
from src.risk.guardrails import DrawdownGuard


def test_notional_limiter_caps():
    nl = NotionalLimiter(per_symbol_cap=Decimal("500"), portfolio_cap=Decimal("1000"))
    assert nl.can_add("ASTER", Decimal("200"))
    nl.apply("ASTER", Decimal("200"))
    assert not nl.can_add("ASTER", Decimal("400"))  # per-symbol cap breach
    assert nl.can_add("B", Decimal("800")) is False  # portfolio cap breach


def test_order_rate_limiter():
    rl = OrderRateLimiter(max_actions_per_min=2)
    assert rl.allow()
    assert rl.allow()
    assert not rl.allow()


def test_drawdown_guard_halts():
    dg = DrawdownGuard(max_drawdown_usd=Decimal("50"))
    dg.update_equity(Decimal("1000"))
    dg.update_equity(Decimal("960"))
    assert not dg.halted()
    dg.update_equity(Decimal("949"))
    assert dg.halted()


