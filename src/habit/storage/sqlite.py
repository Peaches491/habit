"""A local, credential-free :class:`StorageAdapter` backed by SQLite.

Same contract as :class:`~habit.storage.json_file.JsonFileStorageAdapter` —
this is the "BYO-db" adapter from ARCHITECTURE.md, for a durable single-file
store without the JSON adapter's full-file rewrite-per-write. Answers and
derived-score payloads are stored as JSON text in a column (SQLite has no
native dict type), keeping the exact same value shapes either adapter
produces so callers can't tell them apart.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import date
from pathlib import Path

from ..scoring.models import AnswerValue, DayScore
from .base import RawDay, StorageAdapter

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_days (
    date TEXT PRIMARY KEY,
    answers TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scores (
    date TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly (
    week TEXT PRIMARY KEY,
    recap TEXT NOT NULL
);
"""


class SqliteStorageAdapter(StorageAdapter):
    """Stores raw answers and display caches in a SQLite database file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as conn:
            conn.executescript(_SCHEMA)

    @contextlib.contextmanager
    def _session(self):  # type: ignore[no-untyped-def]
        """A connection that commits (or rolls back) *and* closes on exit.

        ``sqlite3.Connection`` used bare as a context manager only handles the
        transaction — it never closes the connection, which would leak a file
        handle on every call since a fresh connection is opened each time.
        """
        conn = sqlite3.connect(self._path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def read_raw(self, since: date) -> list[RawDay]:
        with self._session() as conn:
            rows = conn.execute(
                "SELECT date, answers FROM raw_days WHERE date >= ? ORDER BY date",
                (since.isoformat(),),
            ).fetchall()
        return [RawDay(date=date.fromisoformat(iso), answers=json.loads(blob)) for iso, blob in rows]

    def upsert_raw(self, day: date, answers: dict[str, AnswerValue]) -> None:
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO raw_days (date, answers) VALUES (?, ?)
                ON CONFLICT(date) DO UPDATE SET answers = excluded.answers
                """,
                (day.isoformat(), json.dumps(answers)),
            )

    def write_scores(self, day: date, derived: DayScore) -> None:
        payload = {
            "total": derived.total,
            "items": [
                {
                    "goal": item.goal,
                    "points": item.points,
                    "status": item.status.value,
                    "detail": item.detail,
                }
                for item in derived.items
            ],
        }
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO scores (date, data) VALUES (?, ?)
                ON CONFLICT(date) DO UPDATE SET data = excluded.data
                """,
                (day.isoformat(), json.dumps(payload)),
            )

    def write_weekly(self, week: date, recap: str) -> None:
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO weekly (week, recap) VALUES (?, ?)
                ON CONFLICT(week) DO UPDATE SET recap = excluded.recap
                """,
                (week.isoformat(), recap),
            )
