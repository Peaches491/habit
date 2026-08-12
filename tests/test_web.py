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


def test_get_form_sidebar_shows_current_week(app_and_storage) -> None:
    app, storage, _ = app_and_storage
    today = date.today()
    storage.upsert_raw(today, {"exercise": True, "water": 9, "mood": "great"})
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)

    days = html.split('<a href="/?date=')[1:]
    assert len(days) == 7  # one week, not a trailing window
    today_block = next(b for b in days if b.startswith(f"{today.isoformat()}&"))
    today_head = today_block.split("</a>")[0]
    assert "selected" in today_head and "today" in today_head
    assert "20 pts" in today_head
    other_block = next(b for b in days if not b.startswith(f"{today.isoformat()}&"))
    other_head = other_block.split("</a>")[0]
    assert "selected" not in other_head and "today" not in other_head
    assert ">--<" in other_head
    # "today" is shown via a tinted cell, not literal "(today)" text next to the date.
    assert "(today)" not in html


def test_get_form_sidebar_greys_out_future_days(app_and_storage) -> None:
    app, _, _ = app_and_storage
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    days = html.split('<a href="/?date=')[1:]
    today = date.today()
    for block in days:
        day = date.fromisoformat(block[:10])
        head = block.split("</a>")[0]
        if day > today:
            assert "future" in head, f"{day} should be greyed out as a future day"
        else:
            assert "future" not in head, f"{day} should not be marked future"


def test_get_form_theme_toggle_present(config_path) -> None:
    client = create_app(config_path).test_client()
    html = client.get("/").get_data(as_text=True)
    assert 'id="theme-toggle"' in html
    assert 'aria-label="Toggle light/dark theme"' in html
    # icon-only -- no visible text label alongside it.
    toggle = html[html.index('id="theme-toggle"') : html.index("</button>")]
    assert "Dark mode" not in toggle and "Light mode" not in toggle
    assert 'id="theme-toggle-icon"' in toggle
    # flash-of-wrong-theme guard: applies a saved preference before first paint.
    assert "localStorage.getItem('habit-theme')" in html


def test_get_form_mobile_header_is_full_bleed(config_path) -> None:
    client = create_app(config_path).test_client()
    html = client.get("/").get_data(as_text=True)
    # cancels body's mobile horizontal padding so the header row spans edge to edge.
    assert ".habit-sidebar-header { margin: 0 -1rem; width: calc(100% + 2rem); }" in html


def test_get_form_day_row_scroll_position_persisted(config_path) -> None:
    client = create_app(config_path).test_client()
    html = client.get("/").get_data(as_text=True)
    assert "'habit-days-scroll'" in html
    assert "sessionStorage.getItem(key)" in html
    assert "sessionStorage.setItem(key, el.scrollLeft)" in html
    assert "querySelector('.habit-sidebar-days')" in html


def test_get_form_overall_and_weekly_totals(app_and_storage) -> None:
    app, storage, _ = app_and_storage
    today = date.today()
    storage.upsert_raw(today, {"exercise": True, "water": 9, "mood": "great"})  # 20 pts
    storage.upsert_raw(today - timedelta(days=200), {"exercise": True})  # 10 pts, outside this week
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert "All-time total" in html
    assert "This week" in html
    all_time_pos = html.index("All-time total")
    week_pos = html.index("This week")
    all_time_value = html[all_time_pos : week_pos].split('class="habit-stat-value">')[1].split("<")[0]
    week_value = html[week_pos:].split('class="habit-stat-value">')[1].split("<")[0]
    assert all_time_value == "30 pts"  # 20 (this week) + 10 (200 days ago)
    assert week_value == "20 pts"  # only the currently displayed week


def test_get_form_week_nav_on_current_week_has_no_next(app_and_storage) -> None:
    app, _, _ = app_and_storage
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert "habit-week-arrow disabled" in html
    assert 'aria-label="Previous week"' in html
    # can always go back a week, just never forward past the current one.
    assert 'aria-label="Next week"' not in html


def test_get_form_week_nav_previous_week_allows_next(app_and_storage) -> None:
    app, _, _ = app_and_storage
    client = app.test_client()
    last_week = (date.today() - timedelta(days=7)).isoformat()
    html = client.get(f"/?week={last_week}").get_data(as_text=True)
    assert 'aria-label="Next week"' in html
    assert "habit-week-arrow disabled" not in html


def test_get_form_week_nav_clamped_to_current_week(app_and_storage) -> None:
    app, _, _ = app_and_storage
    client = app.test_client()
    current_week_html = client.get("/").get_data(as_text=True)
    future = (date.today() + timedelta(days=30)).isoformat()
    future_request_html = client.get(f"/?week={future}").get_data(as_text=True)
    # requesting a week far in the future is clamped back to the current one.
    current_days = current_week_html.split('<a href="/?date=')[1:8]
    future_days = future_request_html.split('<a href="/?date=')[1:8]
    assert [b[:10] for b in current_days] == [b[:10] for b in future_days]
    assert "habit-week-arrow disabled" in future_request_html


def test_week_start_config_controls_first_day_of_week(tmp_path) -> None:
    monday_cfg = tmp_path / "monday.yaml"
    monday_cfg.write_text(CONFIG_YAML)  # week_start defaults to monday
    sunday_cfg = tmp_path / "sunday.yaml"
    sunday_cfg.write_text("week_start: sunday\n" + CONFIG_YAML)

    monday_html = create_app(str(monday_cfg)).test_client().get("/").get_data(as_text=True)
    sunday_html = create_app(str(sunday_cfg)).test_client().get("/").get_data(as_text=True)

    monday_first = date.fromisoformat(monday_html.split('<a href="/?date=')[1][:10])
    sunday_first = date.fromisoformat(sunday_html.split('<a href="/?date=')[1][:10])
    assert monday_first.strftime("%A") == "Monday"
    assert sunday_first.strftime("%A") == "Sunday"


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
