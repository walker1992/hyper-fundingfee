from __future__ import annotations

import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional


def setup_app_logger(logger_name: str,
                     *,
                     log_level: str = "INFO",
                     log_file: Optional[str] = None,
                     log_max_bytes: Optional[int] = None,
                     log_backup_count: Optional[int] = None,
                     disable_console_logging: Optional[bool] = None) -> Dict[str, Any]:
    level_str = os.environ.get("LOG_LEVEL", log_level or "INFO")
    try:
        level = getattr(logging, level_str.upper(), logging.INFO)
    except Exception:
        level = logging.INFO

    file_path = os.environ.get("LOG_FILE", log_file or f"logs/{logger_name}.log")
    try:
        d = os.path.dirname(file_path)
        if d:
            os.makedirs(d, exist_ok=True)
    except Exception:
        pass

    max_bytes = 10 * 1024 * 1024 if log_max_bytes is None else int(log_max_bytes)
    backup_count = 5 if log_backup_count is None else int(log_backup_count)
    try:
        max_bytes = int(os.environ.get("LOG_MAX_BYTES", str(max_bytes)))
    except Exception:
        pass
    try:
        backup_count = int(os.environ.get("LOG_BACKUP_COUNT", str(backup_count)))
    except Exception:
        pass

    env_disable = os.environ.get("DISABLE_CONSOLE_LOGGING")
    if env_disable is not None:
        disable_console = env_disable == "1"
    else:
        disable_console = bool(disable_console_logging) if disable_console_logging is not None else False

    logger = logging.getLogger(logger_name)
    try:
        logger.setLevel(level)
    except Exception:
        pass

    fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    try:
        has_file = False
        for h in list(logger.handlers):
            if isinstance(h, RotatingFileHandler):
                has_file = True
                try:
                    h.setFormatter(fmt)
                except Exception:
                    pass
            if disable_console and isinstance(h, logging.StreamHandler):
                try:
                    logger.removeHandler(h)
                except Exception:
                    pass
            elif isinstance(h, logging.StreamHandler):
                try:
                    h.setFormatter(fmt)
                except Exception:
                    pass

        if not has_file:
            fh = RotatingFileHandler(file_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
            fh.setLevel(level)
            fh.setFormatter(fmt)
            logger.addHandler(fh)

        logger.propagate = False
    except Exception:
        pass

    return {
        "file": file_path,
        "level": level_str,
        "max_bytes": max_bytes,
        "backup_count": backup_count,
        "disable_console": disable_console,
    }


class RateLimitedLogger:
    def __init__(self, min_interval_seconds: Optional[Dict[str, int]] = None) -> None:
        self.last_log_times: Dict[str, float] = {}
        self.min_intervals = min_interval_seconds or {
            "default": 60,
            "price_update": 300,
            "connection": 60,
            "api_call": 60,
            "heartbeat": 300,
            "websocket": 120,
        }

    def should_log(self, log_type: str) -> bool:
        now = time.time()
        last_time = self.last_log_times.get(log_type, 0.0)
        interval = self.min_intervals.get(log_type, self.min_intervals["default"])
        if now - last_time >= interval:
            self.last_log_times[log_type] = now
            return True
        return False

    def log(self, logger: logging.Logger, level: str, log_type: str, message: str, *args: Any, **kwargs: Any) -> None:
        if self.should_log(log_type):
            method = getattr(logger, level.lower(), logger.info)
            method(message, *args, **kwargs)


class LogSummarizer:
    def __init__(self, logger: logging.Logger, interval_seconds: int = 300) -> None:
        self.logger = logger
        self.interval = interval_seconds
        self.last_summary_time = time.time()
        self.price_updates: Dict[str, list[tuple[float, float, float]]] = {}
        self.funding_updates: Dict[str, tuple[float, float]] = {}
        self.api_calls = {"success": 0, "failed": 0}
        self.errors: Dict[str, int] = {}
        self.connection_events = {"connect": 0, "disconnect": 0}

    def _check_summary(self) -> None:
        now = time.time()
        if now - self.last_summary_time >= self.interval:
            self._generate_summary()
            self.last_summary_time = now

    def record_price_update(self, symbol: str, exchange: str, old_price: float, new_price: float) -> None:
        key = f"{exchange}_{symbol}"
        self.price_updates.setdefault(key, []).append((old_price, new_price, time.time()))
        self._check_summary()

    def record_funding_update(self, symbol: str, exchange: str, rate: float) -> None:
        key = f"{exchange}_{symbol}"
        self.funding_updates[key] = (rate, time.time())
        self._check_summary()

    def record_api_call(self, success: bool = True) -> None:
        if success:
            self.api_calls["success"] += 1
        else:
            self.api_calls["failed"] += 1
        self._check_summary()

    def record_error(self, error_type: str) -> None:
        self.errors[error_type] = self.errors.get(error_type, 0) + 1
        self._check_summary()

    def record_connection_event(self, event_type: str) -> None:
        if event_type in self.connection_events:
            self.connection_events[event_type] += 1
        self._check_summary()

    def _generate_summary(self) -> None:
        if self.price_updates:
            significant = []
            for key, updates in self.price_updates.items():
                if not updates:
                    continue
                first = updates[0][0] or 0
                last = updates[-1][1]
                cnt = len(updates)
                if first > 0:
                    change_pct = ((last - first) / first) * 100
                    if abs(change_pct) > 0.5 or cnt > 10:
                        significant.append((key, first, last, change_pct, cnt))
            significant.sort(key=lambda x: abs(x[3]), reverse=True)
            top = significant[:5]
            if top:
                parts = []
                for key, first, last, pct, cnt in top:
                    ex, sym = key.split("_")
                    parts.append(f"{ex}/{sym}: {first:.2f}\u2192{last:.2f} ({pct:+.2f}%, {cnt})")
                more = len(significant) - len(top)
                more_text = f" and {more} more" if more > 0 else ""
                self.logger.info(f"Price changes: {', '.join(parts)}{more_text}")

        if self.funding_updates:
            items = sorted(self.funding_updates.items(), key=lambda x: abs(x[1][0]), reverse=True)[:5]
            parts = []
            for key, (rate, _) in items:
                ex, sym = key.split("_")
                parts.append(f"{ex}/{sym}: {rate:+.6f}")
            more = len(self.funding_updates) - len(items)
            more_text = f" and {more} more" if more > 0 else ""
            if parts:
                self.logger.info(f"Funding updates: {', '.join(parts)}{more_text}")

        if self.api_calls["success"] > 0 or self.api_calls["failed"] > 0:
            self.logger.info(f"API calls: ok {self.api_calls['success']}, failed {self.api_calls['failed']}")
            self.api_calls = {"success": 0, "failed": 0}

        if self.errors:
            joined = ", ".join(f"{k}: {v}" for k, v in self.errors.items())
            self.logger.warning(f"Errors: {joined}")
            self.errors = {}

        if self.connection_events["connect"] > 0 or self.connection_events["disconnect"] > 0:
            self.logger.info(f"Connections: up {self.connection_events['connect']}, down {self.connection_events['disconnect']}")
            self.connection_events = {"connect": 0, "disconnect": 0}

        self.price_updates = {}
        self.funding_updates = {}

    def force_summary(self) -> None:
        self._generate_summary()
        self.last_summary_time = time.time()


