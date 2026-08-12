"""Tests for the JSON-file storage adapter."""

from __future__ import annotations

import json
from datetime import date

import pytest

from habit.scoring import DayScore, LineItem, Status
from habit.storage import JsonFileStorageAdapter


@pytest.fixture
def adapter(tmp_path):
    return JsonFileStorageAdapter(tmp_path / "habit_data.json")


def test_read_raw_empty_when_no_file(adapter) -> None:
    assert adapter.read_raw(date(2026, 1, 1)) == []


def test_upsert_then_read_raw_roundtrips(adapter) -> None:
    adapter.upsert_raw(date(2026, 8, 10), {"exercise": True, "water": 9})
    days = adapter.read_raw(date(2026, 1, 1))
    assert len(days) == 1
    assert days[0].date == date(2026, 8, 10)
    assert days[0].answers == {"exercise": True, "water": 9}


def test_upsert_raw_is_idempotent_by_date(adapter) -> None:
    adapter.upsert_raw(date(2026, 8, 10), {"exercise": True})
    adapter.upsert_raw(date(2026, 8, 10), {"exercise": False, "water": 5})
    days = adapter.read_raw(date(2026, 1, 1))
    assert len(days) == 1
    assert days[0].answers == {"exercise": False, "water": 5}


def test_read_raw_filters_by_since_and_sorts(adapter) -> None:
    adapter.upsert_raw(date(2026, 8, 12), {"exercise": True})
    adapter.upsert_raw(date(2026, 8, 10), {"exercise": False})
    adapter.upsert_raw(date(2026, 8, 11), {"exercise": True})
    days = adapter.read_raw(date(2026, 8, 11))
    assert [d.date for d in days] == [date(2026, 8, 11), date(2026, 8, 12)]


def test_write_scores_persists_alongside_raw(adapter, tmp_path) -> None:
    adapter.upsert_raw(date(2026, 8, 10), {"exercise": True})
    score = DayScore(
        items=[LineItem("exercise", 10, Status.SCORED, "yes -> 10 pts")]
    )
    adapter.write_scores(date(2026, 8, 10), score)
    data = (tmp_path / "habit_data.json").read_text()
    assert '"total": 10' in data
    # raw answers are untouched by writing the derived cache
    assert adapter.read_raw(date(2026, 1, 1))[0].answers == {"exercise": True}


def test_write_weekly_persists_recap_text(adapter, tmp_path) -> None:
    adapter.write_weekly(date(2026, 8, 10), "Great week overall.")
    data = json.loads((tmp_path / "habit_data.json").read_text())
    assert data["weekly"]["2026-08-10"] == "Great week overall."
