"""Habit storage: the raw-answer persistence interface and its adapters.

Public API::

    from habit.storage import StorageAdapter, RawDay, JsonFileStorageAdapter

Adapters read/write raw answers only; derived scores/recaps are a disposable
display cache (see :meth:`StorageAdapter.write_scores` /
:meth:`StorageAdapter.write_weekly`). A Google Sheets adapter will implement
the same interface once service-account credentials are available.
"""

from __future__ import annotations

from .base import RawDay, StorageAdapter
from .json_file import JsonFileStorageAdapter

__all__ = ["JsonFileStorageAdapter", "RawDay", "StorageAdapter"]
