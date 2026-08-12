"""A local, credential-free :class:`StorageAdapter` backed by a JSON file.

Fills the same contract the Google Sheets adapter will fill later, so the web
app and (eventually) the agent don't need to know which one is behind
``StorageAdapter``. Good enough for single-user local use; not concurrency-safe
across multiple writers.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..scoring.models import AnswerValue, DayScore
from .base import RawDay, StorageAdapter


class JsonFileStorageAdapter(StorageAdapter):
    """Stores raw answers and display caches as one JSON file on disk."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _load(self) -> dict:
        if not self._path.exists():
            return {"raw": {}, "scores": {}, "weekly": {}}
        with self._path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.replace(self._path)

    def read_raw(self, since: date) -> list[RawDay]:
        data = self._load()
        days = [
            RawDay(date=date.fromisoformat(iso), answers=answers)
            for iso, answers in data["raw"].items()
            if date.fromisoformat(iso) >= since
        ]
        return sorted(days, key=lambda rd: rd.date)

    def upsert_raw(self, day: date, answers: dict[str, AnswerValue]) -> None:
        data = self._load()
        data["raw"][day.isoformat()] = answers
        self._save(data)

    def write_scores(self, day: date, derived: DayScore) -> None:
        data = self._load()
        data["scores"][day.isoformat()] = {
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
        self._save(data)

    def write_weekly(self, week: date, recap: str) -> None:
        data = self._load()
        data["weekly"][week.isoformat()] = recap
        self._save(data)
