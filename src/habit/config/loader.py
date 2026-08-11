"""Load and parse Habit config from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .errors import ConfigError
from .models import Config


def loads(text: str, *, source: str = "<string>") -> Config:
    """Parse a :class:`Config` from a YAML string.

    ``source`` is only used to make error messages point at the right place.
    Raises :class:`ConfigError` on any YAML or validation problem.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {source}: {exc}") from exc

    if data is None:
        raise ConfigError(f"config is empty: {source}")
    if not isinstance(data, dict):
        raise ConfigError(
            f"config root must be a mapping, got {type(data).__name__}: {source}"
        )

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid config in {source}:\n{exc}") from exc


def load(path: str | Path) -> Config:
    """Load a :class:`Config` from a YAML file path.

    Raises :class:`ConfigError` if the file is missing, unreadable, or invalid.
    """
    p = Path(path)
    try:
        text = p.read_text()
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {p}") from exc
    return loads(text, source=str(p))
