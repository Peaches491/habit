"""Tests for the check-in web UI."""

from __future__ import annotations

import json
import re
import textwrap
from datetime import date, timedelta

import pytest

from habit.config import loads
from habit.storage import JsonFileStorageAdapter
from habit.web import build_fields, create_app


CONFIG_YAML = textwrap.dedent(
    """
    title: Daily Habits
    user: daniel
    goals:
      - name: exercise
        description: Did you exercise today?
        type: bool
        icon: fitness_center
        value: 10
      - name: water
        description: How many glasses of water did you drink today?
        type: number
        icon: "\U0001F4A7"
        shortcuts: [3, 5, 9]
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
    assert fields["exercise"].widget == "toggle"
    assert fields["water"].widget == "number"
    assert fields["water"].shortcuts == (3, 5, 9)
    # Short choice lists render as joined toggle buttons, not a dropdown.
    assert fields["mood"].widget == "segmented"
    assert fields["mood"].choices == ("great", "ok", "bad")
    # A judged goal is free text regardless of its declared type.
    assert fields["journal"].widget == "textarea"
    assert fields["journal"].shortcuts is None


def test_build_fields_icons() -> None:
    fields = {f.name: f for f in build_fields(loads(CONFIG_YAML))}
    # A bare identifier is treated as a Material Symbols ligature name.
    assert fields["exercise"].icon == "fitness_center"
    assert fields["exercise"].icon_is_material is True
    # Anything else (an emoji here) renders as a literal glyph.
    assert fields["water"].icon == "\U0001F4A7"
    assert fields["water"].icon_is_material is False
    # No icon configured -> nothing to render.
    assert fields["mood"].icon is None
    assert fields["mood"].icon_is_material is False


def test_build_fields_long_choice_list_falls_back_to_select() -> None:
    config = loads(
        textwrap.dedent(
            """
            goals:
              - name: color
                description: Pick a color
                type: option
                choices: [red, orange, yellow, green, blue, indigo, violet]
                value: 1
            """
        )
    )
    fields = {f.name: f for f in build_fields(config)}
    assert fields["color"].widget == "select"


def test_build_fields_hints() -> None:
    fields = {f.name: f for f in build_fields(loads(CONFIG_YAML))}
    assert fields["exercise"].hint == "10 pts"
    assert "≥ 8" in fields["water"].hint
    assert "vary" in fields["mood"].hint
    assert "agent" in fields["journal"].hint


# --- GET / -------------------------------------------------------------------


def _field_block(html: str, marker: str) -> str:
    """The <div class="habit-field">...</div> chunk containing ``marker``."""
    blocks = html.split('<div class="habit-field">')
    return next(b for b in blocks if marker in b)


def test_get_form_renders_all_goals(config_path) -> None:
    client = create_app(config_path).test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # bool -> a Yes/No toggle button pair (radios styled as joined buttons).
    assert 'name="exercise"' in html and 'type="radio"' in html
    assert 'id="exercise-yes"' in html and 'id="exercise-no"' in html
    assert 'name="water"' in html and 'type="number"' in html
    # water declares numeric shortcuts -> a row of quick-value buttons.
    assert 'data-target="water" data-value="3"' in html
    assert 'data-target="water" data-value="5"' in html
    assert 'data-target="water" data-value="9"' in html
    # short option list -> segmented toggle buttons, not a <select>.
    assert 'name="mood"' in html and "btn-check" in html
    assert 'id="mood-1"' in html and ">great<" in html
    assert 'name="journal"' in html and "<textarea" in html
    assert "Daily Habits - Check-in" in html


def test_get_form_title_falls_back_to_user_or_generic(tmp_path) -> None:
    goal_yaml = "goals: [{name: a, description: A, type: bool, value: 1}]\n"

    with_user = tmp_path / "with_user.yaml"
    with_user.write_text("user: daniel\n" + goal_yaml)
    without_user = tmp_path / "without_user.yaml"
    without_user.write_text(goal_yaml)

    html_with_user = create_app(str(with_user)).test_client().get("/").get_data(as_text=True)
    html_without_user = create_app(str(without_user)).test_client().get("/").get_data(as_text=True)

    assert "Check-in — daniel" in html_with_user
    assert "Daily check-in" in html_without_user


def test_get_form_icon_renders_in_its_own_column(config_path) -> None:
    client = create_app(config_path).test_client()
    html = client.get("/").get_data(as_text=True)
    # exercise's icon is a Material Symbols name -> the ligature font, in its
    # own flush-left column ahead of the field's body (prompt/widget/hint).
    exercise = _field_block(html, 'name="exercise"')
    assert exercise.index("habit-field-icon") < exercise.index("habit-field-body")
    assert 'class="material-symbols-outlined"' in exercise
    assert ">fitness_center<" in exercise
    # water's icon is a literal emoji -> rendered as plain text, no icon font.
    water = _field_block(html, 'name="water"')
    assert water.index("habit-field-icon") < water.index("habit-field-body")
    assert "\U0001F4A7" in water
    assert "material-symbols-outlined" not in water
    # mood has no configured icon -> no icon column at all.
    mood = _field_block(html, 'name="mood"')
    assert "habit-field-icon" not in mood


def test_get_form_has_today_yesterday_quick_select(config_path) -> None:
    client = create_app(config_path).test_client()
    html = client.get("/").get_data(as_text=True)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert 'id="date-today"' in html and f'data-target="_date" data-value="{today}"' in html
    assert 'id="date-yesterday"' in html and f'data-target="_date" data-value="{yesterday}"' in html
    # date field defaults to today, so client-side JS marks Today active on load.
    assert f'id="_date" name="_date" value="{today}"' in html


def test_get_form_date_picker_hidden_behind_reveal_link(config_path) -> None:
    client = create_app(config_path).test_client()
    html = client.get("/").get_data(as_text=True)
    assert '>Logging a different day?</a>' in html
    # the date field (and its value) are still present -> still submits by
    # default even if the user never clicks the link -- just visually hidden.
    date_field = html[html.index('id="date-field"') : html.index('id="_date"')]
    assert "d-none" in date_field


def test_get_form_human_readable_date_heading(config_path) -> None:
    client = create_app(config_path).test_client()
    # date(1776, 2, 6) is a Tuesday per Python's (proleptic Gregorian) calendar.
    html = client.get("/?date=1776-02-06").get_data(as_text=True)
    heading_pos = html.index("Tuesday, February 6th, 1776")
    link_pos = html.index("Logging a different day?")
    total_pos = html.index("Total for 1776-02-06")
    # heading, then the reveal link immediately under it, then the total.
    assert heading_pos < link_pos < total_pos


def test_get_form_running_total_scoring_meta(config_path) -> None:
    client = create_app(config_path).test_client()
    html = client.get("/").get_data(as_text=True)
    assert 'id="habit-running-total"' in html
    meta_json = html.split('id="habit-scoring-meta" type="application/json">')[1].split(
        "</script>"
    )[0]
    meta = json.loads(meta_json)
    assert meta["exercise"] == {"kind": "flat_bool", "points": 10}
    assert meta["water"] == {"kind": "threshold", "at_least": 8.0, "points": 5}
    assert meta["mood"] == {
        "kind": "choice_points",
        "points_by_choice": {"great": 5, "ok": 2, "bad": 0},
    }
    assert meta["journal"] == {"kind": "judged"}


def test_get_form_sidebar_shows_recent_days(app_and_storage) -> None:
    app, storage, _ = app_and_storage
    today = date.today()
    storage.upsert_raw(today, {"exercise": True, "water": 9, "mood": "great"})
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)

    days = html.split('<a href="/?date=')[1:]
    assert len(days) == 14
    # today has an answer -> shows its actual total, not "--", and is selected.
    today_block = days[0]
    assert today_block.startswith(f'{today.isoformat()}" class="habit-day selected"')
    assert "20 pts" in today_block.split("</a>")[0]
    # yesterday has no answer -> shows "--", not selected.
    yesterday_block = days[1]
    assert 'class="habit-day"' in yesterday_block.split("</a>")[0]
    assert ">--<" in yesterday_block.split("</a>")[0]


def test_get_form_selecting_a_day_prefills_the_form(app_and_storage) -> None:
    app, storage, _ = app_and_storage
    storage.upsert_raw(
        date(2026, 8, 5),
        {"exercise": True, "water": 9, "mood": "great", "journal": "Reflected on a good day."},
    )
    client = app.test_client()
    html = client.get("/?date=2026-08-05").get_data(as_text=True)

    assert 'id="_date" name="_date" value="2026-08-05"' in html
    # bool -> the "yes" radio is checked.
    exercise = _field_block(html, 'name="exercise"')
    assert re.search(r'id="exercise-yes"[^>]*checked', exercise)
    assert not re.search(r'id="exercise-no"[^>]*checked', exercise)
    # number -> prefilled value attribute.
    water = _field_block(html, 'name="water"')
    assert 'value="9"' in water
    # segmented option -> the matching choice's radio is checked.
    mood = _field_block(html, 'name="mood"')
    assert re.search(r'id="mood-1"[^>]*checked', mood)  # "great" is choice 1
    # textarea (judged) -> prefilled as inner text.
    journal = _field_block(html, 'name="journal"')
    assert "Reflected on a good day." in journal
    # the human-readable date heading names the day being viewed, so the raw
    # date picker stays tucked behind the reveal link regardless of which day.
    assert "Wednesday, August 5th, 2026" in html
    assert '>Logging a different day?</a>' in html
    date_field = html[html.index('id="date-field"') : html.index('id="_date"')]
    assert "d-none" in date_field


def test_get_form_selecting_an_unlogged_day_is_blank(app_and_storage) -> None:
    app, _, _ = app_and_storage
    client = app.test_client()
    html = client.get("/?date=2026-08-05").get_data(as_text=True)
    exercise = _field_block(html, 'name="exercise"')
    assert "checked" not in exercise
    water = _field_block(html, 'name="water"')
    assert 'value=""' in water


@pytest.fixture
def locked_config_path(tmp_path):
    path = tmp_path / "habit.yaml"
    path.write_text("lock_submitted_days: true\n" + CONFIG_YAML)
    return str(path)


@pytest.fixture
def locked_app_and_storage(locked_config_path, tmp_path):
    storage_path = tmp_path / "habit_data.json"
    storage = JsonFileStorageAdapter(storage_path)
    return create_app(locked_config_path, storage=storage), storage, storage_path


def test_get_form_locked_day_disables_inputs(locked_app_and_storage) -> None:
    app, storage, _ = locked_app_and_storage
    storage.upsert_raw(date(2026, 8, 5), {"exercise": True, "water": 9})
    client = app.test_client()
    html = client.get("/?date=2026-08-05").get_data(as_text=True)

    assert "already logged" in html
    assert "lock_submitted_days" in html
    assert "Submit check-in" not in html
    water = _field_block(html, 'name="water"')
    assert "disabled" in water
    exercise = _field_block(html, 'name="exercise"')
    assert "disabled" in exercise


def test_get_form_unlogged_day_stays_editable_even_when_locking_enabled(
    locked_app_and_storage,
) -> None:
    app, _, _ = locked_app_and_storage
    client = app.test_client()
    html = client.get("/?date=2026-08-05").get_data(as_text=True)
    assert "already logged" not in html
    assert "Submit check-in" in html
    water = _field_block(html, 'name="water"')
    assert "disabled" not in water


def test_post_checkin_rejected_for_locked_day(locked_app_and_storage) -> None:
    app, storage, _ = locked_app_and_storage
    storage.upsert_raw(date(2026, 8, 5), {"exercise": True})
    client = app.test_client()
    resp = client.post("/checkin", data={"_date": "2026-08-05", "exercise": "false"})
    assert resp.status_code == 403
    assert "already logged" in resp.get_data(as_text=True)
    # the original answer must survive -- the locked write was refused.
    days = storage.read_raw(date(2026, 8, 5))
    assert days[0].answers == {"exercise": True}


def test_post_checkin_allowed_for_unlogged_day_when_locking_enabled(
    locked_app_and_storage,
) -> None:
    app, storage, _ = locked_app_and_storage
    client = app.test_client()
    resp = client.post("/checkin", data={"_date": "2026-08-05", "exercise": "true"})
    assert resp.status_code == 200
    days = storage.read_raw(date(2026, 8, 5))
    assert days[0].answers == {"exercise": True}


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


def test_post_checkin_explicit_no_scores_zero_not_skipped(app_and_storage) -> None:
    app, _, _ = app_and_storage
    client = app.test_client()
    resp = client.post(
        "/checkin",
        data={"_date": "2026-08-11", "exercise": "false", "water": "9", "mood": "great"},
    )
    html = resp.get_data(as_text=True)
    # exercise explicitly toggled to No -> scored 0 (not skipped); water clears
    # the threshold -> 5 pts; mood -> 5 pts. journal untouched -> skipped.
    assert "Total:" in html and "10 pts" in html
    assert "badge-nord-success" in html  # exercise: scored, even though 0 pts
    assert "badge-nord-secondary" in html and ">skipped<" in html  # journal


def test_post_checkin_toggle_left_untouched_is_skipped(app_and_storage) -> None:
    app, storage, _ = app_and_storage
    client = app.test_client()
    client.post("/checkin", data={"_date": "2026-08-11"})
    days = storage.read_raw(date(2026, 1, 1))
    assert "exercise" not in days[0].answers


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
