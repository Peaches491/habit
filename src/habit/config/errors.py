"""Errors raised while loading or parsing config."""

from __future__ import annotations


class ConfigError(Exception):
    """Raised when a config file cannot be read or fails validation.

    Wraps lower-level YAML and Pydantic errors so callers (and users who clone
    this repo and author their own YAML) have a single exception type to catch,
    with a message that names the source and explains what is wrong.
    """
