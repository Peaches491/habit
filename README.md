# Habit

A configurable habit tracker. You describe your goals in a YAML file; Habit
prompts you, scores them by your rules, and produces a weekly recap. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the overall design.

This package currently implements the **config format and parser** — the typed
foundation everything else builds on.

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

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

## Development

```bash
pytest
```
