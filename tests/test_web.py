"""Tests for the check-in web UI."""

from __future__ import annotations

import textwrap
from datetime import date

import pytest

from habit.config import loads
from habit.storage import JsonFileStorageAdapter
from habit.web import build_fields, create_app


CONFIG_YAML = textwrap.dedent(
    """
    user: daniel
    goals:
      - name: exercise
        description: Did you exercise today?
        type: bool
        value: 10
      - name: water
        description: Glasses of water?
        type: number
        value:
          type: threshold
          at_least: 8
          points: 5
      - name: mood
        description: How was your mood?
        type: option
        choices: [great, ok, bad]
        value:
          type: options
          points_by_choice: {great: 5, ok: 2, bad: 0}
      - name: journal
        description: What did you reflect on?
        type: bool
        value:
          type: judged
          judge: genuine reflection?
          points: 15
    """
)


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "habit.yaml"
    path.write_text(CONFIG_YAML)
    return str(path)


# --- build_fields ------------------------------------------------------------


def test_build_fields_picks_widget_per_goal() -> None:
    fields = {f.name: f for f in build_fields(loads(CONFIG_YAML))}
    assert fields["exercise"].widget == "checkbox"
    assert fields["water"].widget == "number"
    assert fields["mood"].widget == "select"
    assert fields["mood"].choices == ("great", "ok", "bad")
    # A judged goal is free text regardless of its declared type.
    assert fields["journal"].widget == "textarea"


def test_build_fields_hints() -> None:
    fields = {f.name: f for f in build_fields(loads(CONFIG_YAML))}
    assert fields["exercise"].hint == "10 pts"
    assert "≥ 8" in fields["water"].hint
    assert "vary" in fields["mood"].hint
    assert "agent" in fields["journal"].hint


# --- GET / -------------------------------------------------------------------


def test_get_form_renders_all_goals(config_path) -> None:
    client = create_app(config_path).test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'name="exercise"' in html and 'type="checkbox"' in html
    assert 'name="water"' in html and 'type="number"' in html
    assert 'name="mood"' in html and "<select" in html
    assert "<option value=\"great\"" in html
    assert 'name="journal"' in html and "<textarea" in html
    assert "Check-in — daniel" in html


def test_get_form_bad_config_shows_error(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("goals: []\n")
    client = create_app(str(bad)).test_client()
    resp = client.get("/")
    assert resp.status_code == 400
    assert "Config error" in resp.get_data(as_text=True)


# --- POST /checkin ------------------------------------------------------------


@pytest.fixture
def app_and_storage(config_path, tmp_path):
    storage_path = tmp_path / "habit_data.json"
    storage = JsonFileStorageAdapter(storage_path)
    return create_app(config_path, storage=storage), storage, storage_path


def test_post_checkin_scores_and_stores(app_and_storage) -> None:
    app, storage, _ = app_and_storage
    client = app.test_client()
    resp = client.post(
        "/checkin",
        data={
            "_date": "2026-08-11",
            "exercise": "true",
            "water": "9",
            "mood": "great",
            "journal": "Reflected on a tough day at work.",
        },
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Check-in received" in html
    assert "Total:" in html
    assert "Awaiting judgment" in html  # journal is a judged goal, no verdict yet

    days = storage.read_raw(date(2026, 1, 1))
    assert len(days) == 1
    assert days[0].date == date(2026, 8, 11)
    assert days[0].answers["exercise"] is True
    assert days[0].answers["water"] == 9
    assert days[0].answers["mood"] == "great"


def test_post_checkin_scores_deterministic_goals_immediately(app_and_storage) -> None:
    app, _, _ = app_and_storage
    client = app.test_client()
    resp = client.post(
        "/checkin",
        data={"_date": "2026-08-11", "water": "9", "mood": "great"},
    )
    html = resp.get_data(as_text=True)
    # exercise unchecked -> False -> scored 0, not skipped; water clears the
    # threshold -> 5 pts; mood -> 5 pts. journal untouched -> skipped.
    assert "Total:" in html and "10 pts" in html
    assert "badge-nord-secondary" in html and ">skipped<" in html  # journal


def test_post_checkin_missing_number_is_skipped(app_and_storage) -> None:
    app, storage, _ = app_and_storage
    client = app.test_client()
    client.post("/checkin", data={"_date": "2026-08-11"})
    days = storage.read_raw(date(2026, 1, 1))
    assert "water" not in days[0].answers


def test_post_checkin_bad_number_is_invalid(app_and_storage) -> None:
    app, _, _ = app_and_storage
    client = app.test_client()
    resp = client.post(
        "/checkin", data={"_date": "2026-08-11", "water": "lots"}
    )
    html = resp.get_data(as_text=True)
    assert "badge-nord-danger" in html and ">invalid<" in html


def test_post_checkin_missing_date_defaults_to_today(app_and_storage) -> None:
    app, storage, _ = app_and_storage
    client = app.test_client()
    client.post("/checkin", data={"exercise": "true"})
    days = storage.read_raw(date(2000, 1, 1))
    assert days[0].date == date.today()
