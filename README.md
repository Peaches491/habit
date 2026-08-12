# Habit

A configurable habit tracker. You describe your goals in a YAML file; Habit
prompts you, scores them by your rules, and produces a weekly recap. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the overall design.

This package currently implements the **config format and parser** — the typed
foundation everything else builds on.

## Install

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync
```

This creates a `.venv` and installs the project plus dev dependencies from the
committed `uv.lock`, so you get the exact versions that were tested. Run things
with `uv run`, e.g. `uv run pytest`.

<details><summary>Without uv (pip)</summary>

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .          # runtime deps
pip install pytest        # to run the tests
```
</details>

## Writing your config

Clone the repo and copy [`examples/habit.example.yaml`](examples/habit.example.yaml)
to your own file, then edit it.

```yaml
title: Daily Habits          # optional; shown at the top of the check-in form
user: daniel
timezone: America/Chicago

goals:
  - name: exercise            # unique index key, no spaces
    description: Did you exercise today?
    type: bool                # bool | number | option
    value: 10                 # flat points, OR a rule (see below)
```

`title`, `user`, and `timezone` are all optional metadata. The check-in form's
heading is `"<title> - Check-in"` if `title` is set, else `"Check-in — <user>"`,
else a generic `"Daily check-in"`.

`lock_submitted_days` (default `false`): if `true`, a day that's already been
logged can no longer be edited — its form renders read-only (no submit
button) and a resubmission is rejected server-side too, not just hidden in
the UI. Days that haven't been logged yet stay editable either way.

`week_start` (default `monday`; also accepts `sunday`): which day the
sidebar's week view starts on.

### Goal fields

| Field         | Required | Notes |
|---------------|----------|-------|
| `name`        | yes      | Unique index key. No spaces; letters, digits, `_`, `-`. |
| `description` | yes      | The prompt / what the goal means. |
| `type`        | yes      | `bool`, `number`, or `option`. |
| `icon`        | no       | An emoji or a [Material Symbols](https://fonts.google.com/icons) name (e.g. `local_drink`); shown before the prompt. |
| `choices`     | option only | A non-empty list of allowed values; forbidden on other types. |
| `shortcuts`   | number only | A non-empty list of common values, rendered as quick-select buttons below the field; forbidden on judged goals (rendered as free text). |
| `value`       | yes      | Either a flat integer (points) **or** a rule (a mapping with a `type`). |

### Rules

A rule is a mapping with a `type` discriminator that encodes richer scoring than
a flat point value:

| `type`      | Valid on | Fields | Meaning |
|-------------|----------|--------|---------|
| `threshold` | number   | `at_least`, `points` | Award `points` when the logged value ≥ `at_least`. |
| `options`   | option   | `points_by_choice` | Points depend on which choice was selected (keys must be declared `choices`). |
| `judged`    | any      | `judge`, `points`  | The agent judges the entry against the `judge` prompt; full `points` or zero. |

The parser validates *structure* only — how points actually accrue on a given
day lives in the rule engine, not the config layer.

## Usage

```python
from habit.config import load

config = load("habit.yaml")
for name, goal in config.by_name.items():
    print(name, goal.type, goal.value)
```

Invalid config raises `habit.config.ConfigError` with a message naming the
source and the problem. Unknown keys are rejected, so typos surface immediately.

## Scoring

The rule engine turns a day's raw answers into awarded points. It's pure and
deterministic — the same inputs always produce the same result, so scores can
be recomputed on read rather than cached.

```python
from habit.config import load
from habit.scoring import score_day, DayLog, JudgeVerdict

config = load("habit.yaml")
day = DayLog(
    answers={"exercise": True, "water": 9, "mood": "great"},
    verdicts={},  # agent verdicts for judged goals, once decided
)
result = score_day(config, day)
result.total                       # summed points ("additive line items")
result.by_goal["water"].detail     # "9 >= 8.0 -> 5 pts" (an audit trace)
result.pending                     # judged goals still awaiting a verdict
```

The engine never calls a model. A `judged` goal with no verdict yet comes back
as a `JudgeRequest` in `result.pending`; the agent decides, and you re-score
with the verdict recorded in `DayLog.verdicts`. Each line item carries a
`status` (`scored`, `skipped`, `pending_judgment`, `invalid`) and a
human-readable `detail`.

## Check-in web UI

A small Flask app serves a daily check-in form auto-generated from your config —
one field per goal, with the widget chosen by the goal's type: a Yes/No toggle
for `bool`, a number input for `number` (plus quick-select buttons if the goal
declares `shortcuts`), joined toggle buttons for a short `option` list (a
dropdown past 5 choices), and a text box for agent-judged goals. A running
point total updates live as you fill out the form (a client-side preview of
the deterministic rules only — judged goals count as pending; the real total
is always recomputed server-side by `habit.scoring` on submit). The date field
defaults to today and stays tucked behind a "Logging a different day?" link,
with its own Today/Yesterday quick-select once revealed.

A full-height sidebar (anchored to the edge of the page, with an app logo at
top that links back to `/`) shows one week at a time — 7 days, each with its
point total (or `--` if not logged yet) — with prev/next arrows to page
through weeks (never into a future one) and a light/dark theme toggle
underneath the logo. It also shows a highlighted all-time point total and the
current week's total. Clicking a day reloads the form pre-filled with that
day's answers — handy for reviewing or amending a past entry. If
`lock_submitted_days` is set, a day that's already logged renders read-only
instead.

```bash
uv run python -m habit.web --config habit.yaml --storage habit_data.json
# then open http://127.0.0.1:5000
```

The config is reloaded on each request, so editing the YAML just needs a page
refresh. Submitting the form stores the day's raw answers (via a
`StorageAdapter`), scores them immediately, and shows the line items, total,
and any agent-judged goals still awaiting a verdict.

### Storage backends

`--storage` picks the backend by file extension — `.db` / `.sqlite` /
`.sqlite3` for SQLite, anything else for a JSON file:

```bash
uv run python -m habit.web --config habit.yaml --storage habit_data.db
```

Both implement the same `StorageAdapter` interface (`habit.storage`), so the
web app and any future caller never need to know which one is behind it.
Google Sheets will be a third adapter behind the same interface once
service-account credentials are available.

## Development

```bash
uv run pytest
```

When you change dependencies in `pyproject.toml`, run `uv lock` and commit the
updated `uv.lock`. CI runs with `--frozen`, so it fails if the lock is stale.
