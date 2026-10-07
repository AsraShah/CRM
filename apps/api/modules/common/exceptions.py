"""Domain errors and the uniform API error envelope (section 6.2).

Every error response carries ``code``, ``message``, ``field_errors`` and
``request_id``. Responses never expose stack traces, and a 404 never reveals
whether a record exists in another tenant.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from modules.common.tenancy import MissingWorkspaceContext

logger = logging.getLogger("scalevexo.api")


class DomainError(Exception):
    """Base class for rule violations raised by service functions."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        field_errors: dict[str, list[str]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.field_errors = field_errors or {}
        self.extra = extra or {}


class ValidationFailed(DomainError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "validation_failed"


class NotAuthorized(DomainError):
    """The actor is known but may not perform this action."""

    status_code = status.HTTP_403_FORBIDDEN
    default_code = "not_authorized"


class NotFound(DomainError):
    """The record does not exist, or is not visible to this actor.

    Deliberately indistinguishable from each other: distinguishing them would
    confirm the existence of another tenant's record.
    """

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


class VersionConflict(ConflictError):
    """Optimistic concurrency failure.

    Carries the current version so the client can reload and let the user
    reconcile. The server never silently retries an outdated write.
    """

    default_code = "version_conflict"

    def __init__(self, *, expected: int, current: int) -> None:
        super().__init__(
            "This record changed since you loaded it. Reload and reapply your edit.",
            extra={"expected_version": expected, "current_version": current},
        )


class TransitionNotAllowed(DomainError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "transition_not_allowed"


class PayloadTooLarge(DomainError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_code = "payload_too_large"


class BudgetExceeded(DomainError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "budget_exceeded"


def _envelope(
    *, code: str, message: str, field_errors: dict, request_id: str, extra: dict
) -> dict[str, Any]:
    body = {
        "code": code,
        "message": message,
        "field_errors": field_errors,
        "request_id": request_id,
    }
    body.update(extra)
    return body


def scalevexo_exception_handler(exc, context):
    """DRF exception handler producing the documented error envelope."""
    request = context.get("request")
    request_id = getattr(request, "request_id", "") if request else ""

    if isinstance(exc, DomainError):
        return Response(
            _envelope(
                code=exc.code,
                message=exc.message,
                field_errors=exc.field_errors,
                request_id=request_id,
                extra=exc.extra,
            ),
            status=exc.status_code,
        )

    if isinstance(exc, MissingWorkspaceContext):
        # A programming error, not a client error: a tenant-scoped operation ran
        # without a context. Log it, but tell the caller nothing beyond "denied".
        logger.error("Tenant-scoped operation ran without workspace context: %s", exc)
        return Response(
            _envelope(
                code="workspace_context_required",
                message="No workspace context is active for this request.",
                field_errors={},
                request_id=request_id,
                extra={},
            ),
            status=status.HTTP_403_FORBIDDEN,
        )

    if isinstance(exc, Http404):
        return Response(
            _envelope(
                code="not_found",
                message="The requested record does not exist or is not visible to you.",
                field_errors={},
                request_id=request_id,
                extra={},
            ),
            status=status.HTTP_404_NOT_FOUND,
        )

    if isinstance(exc, PermissionDenied):
        return Response(
            _envelope(
                code="not_authorized",
                message="You are not permitted to perform this action.",
                field_errors={},
                request_id=request_id,
                extra={},
            ),
            status=status.HTTP_403_FORBIDDEN,
        )

    if isinstance(exc, DjangoValidationError):
        return Response(
            _envelope(
                code="validation_failed",
                message="The request could not be validated.",
                field_errors=getattr(exc, "message_dict", {}),
                request_id=request_id,
                extra={},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        # Unhandled. Log with context; return an opaque message.
        logger.exception("Unhandled API exception", extra={"request_id": request_id})
        return Response(
            _envelope(
                code="internal_error",
                message="The request could not be completed.",
                field_errors={},
                request_id=request_id,
                extra={},
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    detail = response.data
    field_errors = detail if isinstance(detail, dict) and "detail" not in detail else {}
    message = (
        str(detail.get("detail"))
        if isinstance(detail, dict) and "detail" in detail
        else "The request could not be completed."
    )
    response.data = _envelope(
        code=getattr(exc, "default_code", "error"),
        message=message,
        field_errors=field_errors,
        request_id=request_id,
        extra={},
    )
    return response
