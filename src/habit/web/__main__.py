"""Run the check-in web UI: ``python -m habit.web --config habit.yaml``."""

from __future__ import annotations

import argparse
import os

from ..storage import JsonFileStorageAdapter
from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="habit.web", description="Serve the Habit daily check-in form."
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("HABIT_CONFIG", "habit.yaml"),
        help="Path to the habit config YAML (default: $HABIT_CONFIG or habit.yaml).",
    )
    parser.add_argument(
        "--storage",
        default=os.environ.get("HABIT_STORAGE", "habit_data.json"),
        help="Path to the JSON storage file (default: $HABIT_STORAGE or habit_data.json).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    app = create_app(args.config, storage=JsonFileStorageAdapter(args.storage))
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
