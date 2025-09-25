import json
import os
import tempfile
from decimal import Decimal

from src.core.clock import TimeProvider
from src.core.config import load_config
from src.core.metrics import Metrics
from src.core.persistence import Event, StateStore
from src.core.types import Order, Position


def test_clock_now_and_sleep():
    tp = TimeProvider()
    t0 = tp.now()
    assert isinstance(t0, float)


def test_config_loads_from_example(tmp_path):
    example = {
        "credentials": {"account_address": "0x1", "secret_key": "0x2", "base_url": "https://api"},
        "markets": {"base": "ASTER", "spot": "ASTER/USDT", "perp": "ASTER"},
        "strategy": {"enter_threshold_apr": 0.1, "exit_threshold_apr": 0.04, "target_usd_notional": 200.0, "hedge_ratio": 1.0},
        "execution": {"price_offset_ticks": 1, "tif": "Gtc", "post_only": True, "reprice_interval_ms": 800, "max_replaces_per_min": 20},
        "risk": {"per_symbol_notional_cap": 500.0, "portfolio_notional_cap": 2000.0, "max_drawdown_usd": 50.0, "min_spread_ticks": 1},
        "telemetry": {"log_level": "INFO", "metrics": True},
    }
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(example))
    cfg = load_config(str(path))
    assert cfg.strategy.enter_threshold_apr == Decimal("0.1")
    assert cfg.markets["spot"] == "ASTER/USDT"


def test_metrics_counter_and_gauge():
    m = Metrics()
    c = m.counter("orders")
    c.inc()
    c.inc(2)
    assert c.value == 3
    g = m.gauge("pnl")
    g.set(1.5)
    assert g.value == 1.5


def test_persistence_kv_and_events(tmp_path):
    db = tmp_path / "state.db"
    store = StateStore(str(db))
    store.put("positions", {"ASTER": {"base": 1}})
    got = store.get("positions")
    assert got == {"ASTER": {"base": 1}}
    store.append_event(Event(ts=1.0, kind="order", data={"oid": 1}))
    events = list(store.iter_events("order"))
    assert len(events) == 1 and events[0].data["oid"] == 1
    store.close()


def test_types_dataclasses():
    o = Order(oid=None, symbol="ASTER", side="BUY", qty=Decimal("1"), price=Decimal("10"), tif="Gtc", flags={"post_only": True}, status="new")
    p = Position(symbol="ASTER", base=Decimal("1"), avg_price=Decimal("10"), realized_pnl=Decimal("0"), funding_accrual=Decimal("0"))
    assert o.symbol == "ASTER" and p.base == Decimal("1")


