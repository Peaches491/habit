"""Habit storage: the raw-answer persistence interface and its adapters.

Public API::

    from habit.storage import StorageAdapter, RawDay, adapter_for_path
    from habit.storage import JsonFileStorageAdapter, SqliteStorageAdapter

Adapters read/write raw answers only; derived scores/recaps are a disposable
display cache (see :meth:`StorageAdapter.write_scores` /
:meth:`StorageAdapter.write_weekly`). A Google Sheets adapter will implement
the same interface once service-account credentials are available.
"""

from __future__ import annotations

from pathlib import Path

from .base import RawDay, StorageAdapter
from .json_file import JsonFileStorageAdapter
from .sqlite import SqliteStorageAdapter

__all__ = [
    "JsonFileStorageAdapter",
    "RawDay",
    "SqliteStorageAdapter",
    "StorageAdapter",
    "adapter_for_path",
]

# Extensions that select the SQLite adapter; anything else (including no
# extension) falls back to the JSON file adapter.
_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def adapter_for_path(path: str | Path) -> StorageAdapter:
    """Pick a :class:`StorageAdapter` by ``path``'s file extension.

    ``.db`` / ``.sqlite`` / ``.sqlite3`` -> :class:`SqliteStorageAdapter`;
    anything else -> :class:`JsonFileStorageAdapter`. Both implement the same
    interface, so callers never need to know which one they got.
    """
    path = Path(path)
    if path.suffix in _SQLITE_SUFFIXES:
        return SqliteStorageAdapter(path)
    return JsonFileStorageAdapter(path)
