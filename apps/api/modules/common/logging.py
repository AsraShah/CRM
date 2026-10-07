"""Structured logging (SVX-TECH-001 section 12.1).

Records carry request_id, workspace, job id, action and outcome. They must not
carry credentials, message bodies or sensitive request payloads.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from modules.common.tenancy import current_workspace_id_or_none

# Default is None rather than {}: a mutable default on a ContextVar is a single
# shared object, so anything that mutated it in place would leak context between
# unrelated requests. Readers go through _context(), which always returns a
# fresh mapping.
_request_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "scalevexo_log_context", default=None
)


def _context() -> dict[str, Any]:
    return _request_context.get() or {}


# Attributes the stdlib puts on every record; anything else the caller attached
# via `extra=` is treated as structured context worth emitting.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def bind_request_context(**values: Any) -> None:
    _request_context.set({**_context(), **values})


def clear_request_context() -> None:
    _request_context.set(None)


class RequestContextFilter(logging.Filter):
    """Merge ambient request and workspace context onto each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _context().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        if not hasattr(record, "workspace_id"):
            workspace_id = current_workspace_id_or_none()
            if workspace_id is not None:
                record.workspace_id = str(workspace_id)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for ingestion by the host log shipper."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = _safe(value)
        if record.exc_info:
            # Type and message only. Full tracebacks can embed request data.
            exc_type, exc_value, _ = record.exc_info
            payload["exception"] = {
                "type": getattr(exc_type, "__name__", str(exc_type)),
                "message": str(exc_value),
            }
        return json.dumps(payload, default=str, separators=(",", ":"))


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)
