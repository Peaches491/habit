"""Tests for the storage adapters (JSON file and SQLite) and adapter_for_path.

The conformance tests run against both adapters via a parametrized fixture,
so both backends are held to the same StorageAdapter contract. Backend-specific
tests (below) check the actual on-disk shape each one produces.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from habit.scoring import DayScore, LineItem, Status
from habit.storage import (
    JsonFileStorageAdapter,
    SqliteStorageAdapter,
    adapter_for_path,
)

# --- Conformance: every StorageAdapter must behave the same way -------------


@pytest.fixture(params=["json", "sqlite"])
def adapter(request, tmp_path):
    if request.param == "json":
        return JsonFileStorageAdapter(tmp_path / "habit_data.json")
    return SqliteStorageAdapter(tmp_path / "habit_data.db")


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


def test_write_scores_leaves_raw_untouched(adapter) -> None:
    adapter.upsert_raw(date(2026, 8, 10), {"exercise": True})
    score = DayScore(items=[LineItem("exercise", 10, Status.SCORED, "yes -> 10 pts")])
    adapter.write_scores(date(2026, 8, 10), score)  # disposable cache write, must not raise
    assert adapter.read_raw(date(2026, 1, 1))[0].answers == {"exercise": True}


def test_write_weekly_does_not_raise(adapter) -> None:
    adapter.write_weekly(date(2026, 8, 10), "Great week overall.")


# --- JSON-file-specific: the actual file shape it produces -------------------


@pytest.fixture
def json_adapter(tmp_path):
    return JsonFileStorageAdapter(tmp_path / "habit_data.json"), tmp_path / "habit_data.json"


def test_json_write_scores_persists_total(json_adapter) -> None:
    adapter, path = json_adapter
    adapter.upsert_raw(date(2026, 8, 10), {"exercise": True})
    score = DayScore(items=[LineItem("exercise", 10, Status.SCORED, "yes -> 10 pts")])
    adapter.write_scores(date(2026, 8, 10), score)
    data = json.loads(path.read_text())
    assert data["scores"]["2026-08-10"]["total"] == 10


def test_json_write_weekly_persists_recap_text(json_adapter) -> None:
    adapter, path = json_adapter
    adapter.write_weekly(date(2026, 8, 10), "Great week overall.")
    data = json.loads(path.read_text())
    assert data["weekly"]["2026-08-10"] == "Great week overall."


# --- SQLite-specific: the actual schema/rows it produces ---------------------


@pytest.fixture
def sqlite_adapter(tmp_path):
    path = tmp_path / "habit_data.db"
    return SqliteStorageAdapter(path), path


def test_sqlite_creates_db_file_with_schema(sqlite_adapter) -> None:
    _, path = sqlite_adapter
    assert path.exists()
    with sqlite3.connect(path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"raw_days", "scores", "weekly"} <= tables


def test_sqlite_upsert_raw_writes_one_row_per_date(sqlite_adapter) -> None:
    adapter, path = sqlite_adapter
    adapter.upsert_raw(date(2026, 8, 10), {"exercise": True})
    adapter.upsert_raw(date(2026, 8, 10), {"exercise": False})  # amend, not a new row
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT date, answers FROM raw_days").fetchall()
    assert rows == [("2026-08-10", json.dumps({"exercise": False}))]


def test_sqlite_write_scores_persists_total(sqlite_adapter) -> None:
    adapter, path = sqlite_adapter
    score = DayScore(items=[LineItem("exercise", 10, Status.SCORED, "yes -> 10 pts")])
    adapter.write_scores(date(2026, 8, 10), score)
    with sqlite3.connect(path) as conn:
        (data,) = conn.execute("SELECT data FROM scores WHERE date = ?", ("2026-08-10",)).fetchone()
    assert json.loads(data)["total"] == 10


def test_sqlite_write_weekly_persists_recap_text(sqlite_adapter) -> None:
    adapter, path = sqlite_adapter
    adapter.write_weekly(date(2026, 8, 10), "Great week overall.")
    with sqlite3.connect(path) as conn:
        (recap,) = conn.execute("SELECT recap FROM weekly WHERE week = ?", ("2026-08-10",)).fetchone()
    assert recap == "Great week overall."


# --- adapter_for_path ---------------------------------------------------------


@pytest.mark.parametrize("suffix", [".db", ".sqlite", ".sqlite3"])
def test_adapter_for_path_picks_sqlite(tmp_path, suffix) -> None:
    adapter = adapter_for_path(tmp_path / f"data{suffix}")
    assert isinstance(adapter, SqliteStorageAdapter)


@pytest.mark.parametrize("suffix", [".json", ""])
def test_adapter_for_path_picks_json(tmp_path, suffix) -> None:
    adapter = adapter_for_path(tmp_path / f"data{suffix}")
    assert isinstance(adapter, JsonFileStorageAdapter)
