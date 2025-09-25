from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Counter:
    _value: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, n: int = 1) -> None:
        with self._lock:
            self._value += n

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


@dataclass
class Gauge:
    _value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, v: float) -> None:
        with self._lock:
            self._value = float(v)

    @property
    def value(self) -> float:
        with self._lock:
            return self._value


class Metrics:
    def __init__(self) -> None:
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._lock = threading.Lock()

    def counter(self, name: str) -> Counter:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = Counter()
            return self._counters[name]

    def gauge(self, name: str) -> Gauge:
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = Gauge()
            return self._gauges[name]


