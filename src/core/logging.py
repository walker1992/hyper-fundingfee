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
            handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            self._logger.addHandler(handler)
        else:
            # Ensure existing stream handlers use consistent formatter
            try:
                for h in list(self._logger.handlers):
                    if isinstance(h, logging.StreamHandler):
                        h.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            except Exception:
                pass
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

    def _format_value(self, value: Any) -> str:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (dict, list, tuple)):
            try:
                return json.dumps(value, ensure_ascii=False, separators=(",", ":"), cls=self._EnhancedJSONEncoder)
            except Exception:
                return str(value)
        return str(value)

    def log(self, level: int, message: str, **fields: Any) -> None:
        # Plain key=value fields appended to message; timestamp and level come from formatter
        try:
            if fields:
                extras = " ".join(f"{k}={self._format_value(v)}" for k, v in fields.items())
                line = f"{message} {extras}"
            else:
                line = message
        except Exception:
            line = message
        self._logger.log(level, line)

    def info(self, message: str, **fields: Any) -> None:
        self.log(logging.INFO, message, **fields)

    def warn(self, message: str, **fields: Any) -> None:
        self.log(logging.WARNING, message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self.log(logging.ERROR, message, **fields)


