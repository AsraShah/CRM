#!/usr/bin/env python
"""Django management entrypoint for the ScaleVexo CRM API."""

import os
import sys


def main() -> None:
    # Load .env before the settings module reads os.environ. Exported values
    # still win, so `FOO=bar manage.py ...` overrides the file.
    from config.envfile import load_env

    load_env()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ImportError(
            "Django is not importable. Activate the virtualenv and run `uv sync`."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
