from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


@dataclass
class Event:
    ts: float
    kind: str
    data: Dict[str, Any]


class StateStore:
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._setup()

    class _EnhancedJSONEncoder(json.JSONEncoder):
        def default(self, o: Any):  # type: ignore[override]
            if isinstance(o, Decimal):
                return str(o)
            try:
                return super().default(o)
            except TypeError:
                # Fallback to string representation for non-serializable objects
                return str(o)

    @staticmethod
    def _dumps_safe(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, cls=StateStore._EnhancedJSONEncoder)

    def _setup(self) -> None:
        c = self._conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS kv (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def put(self, key: str, value: Dict[str, Any]) -> None:
        payload = self._dumps_safe(value)
        self._conn.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (key, payload))
        self._conn.commit()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        cur = self._conn.execute("SELECT v FROM kv WHERE k = ?", (key,))
        row = cur.fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def append_event(self, event: Event) -> None:
        payload = self._dumps_safe(event.data)
        self._conn.execute("INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)", (event.ts, event.kind, payload))
        self._conn.commit()

    def iter_events(self, kind: Optional[str] = None) -> Iterable[Event]:
        if kind is None:
            cur = self._conn.execute("SELECT ts, kind, data FROM events ORDER BY id ASC")
        else:
            cur = self._conn.execute("SELECT ts, kind, data FROM events WHERE kind = ? ORDER BY id ASC", (kind,))
        for ts, k, data in cur.fetchall():
            yield Event(ts=ts, kind=k, data=json.loads(data))

    def close(self) -> None:
        self._conn.close()


