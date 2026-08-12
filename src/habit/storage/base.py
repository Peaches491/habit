"""The storage interface: adapters read/write *raw* answers only.

Raw answers are the source of truth (hand-editable); derived scores and
weekly recaps are a disposable display cache, recomputed on read and never
trusted as persisted state. See ARCHITECTURE.md for the full rationale.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from ..scoring.models import AnswerValue, DayScore


@dataclass(frozen=True)
class RawDay:
    """One day's raw answers, as read back from an adapter."""

    date: date
    answers: dict[str, AnswerValue] = field(default_factory=dict)


class StorageAdapter(ABC):
    """Reads/writes raw answers; Sheets, SQLite, etc. all implement this."""

    @abstractmethod
    def read_raw(self, since: date) -> list[RawDay]:
        """Return raw days on or after ``since``, oldest first."""

    @abstractmethod
    def upsert_raw(self, day: date, answers: dict[str, AnswerValue]) -> None:
        """Idempotently write one day's raw answers (create, backfill, or amend)."""

    @abstractmethod
    def write_scores(self, day: date, derived: DayScore) -> None:
        """Materialize a day's derived score for display. Disposable cache."""

    @abstractmethod
    def write_weekly(self, week: date, recap: str) -> None:
        """Materialize a week's recap text for display. Disposable cache."""
