from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional


@dataclass
class JsonLogger:
    name: str = "app"
    level: int = logging.INFO

    def __post_init__(self) -> None:
        self._logger = logging.getLogger(self.name)
        self._logger.setLevel(self.level)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
        # Prevent duplicate logs via root logger
        self._logger.propagate = False

    class _EnhancedJSONEncoder(json.JSONEncoder):
        def default(self, o: Any):  # type: ignore[override]
            if isinstance(o, Decimal):
                return str(o)
            try:
                return super().default(o)
            except TypeError:
                return str(o)

    def log(self, level: int, message: str, **fields: Any) -> None:
        record: Dict[str, Any] = {
            "ts": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "lvl": logging.getLevelName(level),
            "msg": message,
        }
        if fields:
            record.update(fields)
        self._logger.log(level, json.dumps(record, ensure_ascii=False, cls=self._EnhancedJSONEncoder))

    def info(self, message: str, **fields: Any) -> None:
        self.log(logging.INFO, message, **fields)

    def warn(self, message: str, **fields: Any) -> None:
        self.log(logging.WARNING, message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self.log(logging.ERROR, message, **fields)


