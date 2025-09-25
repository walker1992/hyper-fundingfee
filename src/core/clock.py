from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class TimeProvider:
    now_fn: Callable[[], float] = time.time
    sleep_fn: Callable[[float], None] = time.sleep

    def now(self) -> float:
        return float(self.now_fn())

    def sleep(self, seconds: float) -> None:
        self.sleep_fn(seconds)


