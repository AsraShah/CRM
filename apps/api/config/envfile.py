"""Minimal .env loader.

The settings modules read ``os.environ`` directly, which is the right thing for
a container: the orchestrator supplies the environment. A developer machine has
no orchestrator, so something has to read the file the setup runbook tells
people to create.

Deliberately about twenty lines rather than a dependency. It does one thing:
KEY=value, ignoring blanks and comments, and it **never overwrites a variable
that is already set** — an explicitly exported value must win over the file, or
overriding a setting for one command becomes impossible.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repository root, three levels up from this file (config/ -> api/ -> apps/).
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def load_env(path: Path | str | None = None) -> int:
    """Load KEY=value pairs into os.environ. Returns how many were set.

    A missing file is not an error: in production the environment comes from
    the container, and there is no file to find.
    """
    env_path = Path(path) if path else DEFAULT_ENV_PATH
    if not env_path.is_file():
        return 0

    loaded = 0
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Strip one layer of matching quotes, so both KEY=value and
        # KEY="value with spaces" behave as written.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
