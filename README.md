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
user: daniel
timezone: America/Chicago

goals:
  - name: exercise            # unique index key, no spaces
    description: Did you exercise today?
    type: bool                # bool | number | option
    value: 10                 # flat points, OR a rule (see below)
```

### Goal fields

| Field         | Required | Notes |
|---------------|----------|-------|
| `name`        | yes      | Unique index key. No spaces; letters, digits, `_`, `-`. |
| `description` | yes      | The prompt / what the goal means. |
| `type`        | yes      | `bool`, `number`, or `option`. |
| `choices`     | option only | A non-empty list of allowed values; forbidden on other types. |
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

## Development

```bash
uv run pytest
```

When you change dependencies in `pyproject.toml`, run `uv lock` and commit the
updated `uv.lock`. CI runs with `--frozen`, so it fails if the lock is stale.
