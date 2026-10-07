"""Shared persistence conventions (SVX-TECH-001 section 4.1).

UUID primary keys, UTC timestamptz, created_at / updated_at, an integer version
on editable records, and workspace_id on every business table.

Null means unknown. Zero is a real value. No business amount is ever stored as
a float.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from modules.common.tenancy import current_workspace_id

# Business money. numeric(18,2) with an explicit ISO currency code alongside.
MONEY_MAX_DIGITS = 18
MONEY_DECIMAL_PLACES = 2


class TimestampedModel(models.Model):
    """UUID key plus creation and update instants."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class WorkspaceScopedQuerySet(models.QuerySet):
    """QuerySet that refuses to run without a workspace context.

    Row-level security is the real boundary. This is the second, application
    level check demanded by section 4.2 -- it turns an accidental unscoped query
    into an immediate, obvious failure during development instead of a silently
    empty result set.
    """

    def for_current_workspace(self) -> WorkspaceScopedQuerySet:
        return self.filter(workspace_id=current_workspace_id())

    def alive(self) -> WorkspaceScopedQuerySet:
        """Exclude soft-deleted records, as ordinary queries must."""
        return self.filter(deleted_at__isnull=True)


class WorkspaceScopedManager(models.Manager.from_queryset(WorkspaceScopedQuerySet)):
    """Default manager. Note that it does not auto-scope.

    Auto-scoping every manager read is tempting but hides the boundary and
    breaks the scheduler and migrations. Selectors call
    ``.for_current_workspace()`` explicitly so that each read states its scope.
    """


class WorkspaceScopedModel(TimestampedModel):
    """Base class for tenant-owned business records."""

    workspace = models.ForeignKey(
        "identity.Workspace",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
    )
    # Optimistic concurrency. Services require an expected version on update and
    # return 409 on mismatch rather than silently overwriting (section 6.2).
    version = models.PositiveIntegerField(default=1)
    # Soft deletion is not erasure and must never be described as such
    # (section 4.2).
    deleted_at = models.DateTimeField(null=True, blank=True, default=None)
    deleted_reason = models.TextField(blank=True, default="")

    objects = WorkspaceScopedManager()

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class AuditAction(models.TextChoices):
    CREATE = "create", "Create"
    UPDATE = "update", "Update"
    DELETE = "delete", "Delete"
    TRANSITION = "transition", "State transition"
    LOGIN = "login", "Login"
    LOGIN_FAILED = "login_failed", "Failed login"
    PERMISSION_DENIED = "permission_denied", "Permission denied"
    EXPORT = "export", "Export"
    IMPORT = "import", "Import"
    RULE_ACTION = "rule_action", "Automated rule action"
    CORRECTION = "correction", "Correction"


class AuditEvent(models.Model):
    """Append-only record of who changed what (CRM10, section 5.4).

    The application database role is granted INSERT and SELECT only -- no UPDATE
    and no DELETE (see the RLS migration). These records therefore resist
    ordinary user tampering. They are *not* proof against a compromised database
    administrator, and must not be presented as such.

    Retention is performed by a separate maintenance identity under a recorded
    procedure.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="audit_events"
    )
    # The actor is preserved even if the membership is later suspended or the
    # record reassigned: reassignment must not rewrite history (CRM01).
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor_label = models.CharField(max_length=320, blank=True, default="")
    action = models.CharField(max_length=32, choices=AuditAction.choices)
    entity_type = models.CharField(max_length=64)
    entity_id = models.UUIDField(null=True, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    request_id = models.CharField(max_length=64, blank=True, default="")
    correlation_id = models.UUIDField(null=True, blank=True)
    # Redacted before/after snapshots. Never contains passwords, tokens or
    # provider credentials -- see modules.common.audit.redact.
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True, default="")
    # Links a correction to the event it corrects (Journey D). Corrections
    # append; they never overwrite.
    corrects = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="corrections",
    )

    class Meta:
        db_table = "common_audit_event"
        indexes = [
            models.Index(fields=["workspace", "entity_type", "entity_id", "-occurred_at"]),
            models.Index(fields=["workspace", "-occurred_at"]),
            models.Index(fields=["workspace", "actor", "-occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.entity_type}:{self.entity_id}"


class IdempotencyRecord(models.Model):
    """Binds (workspace, actor, route, key) to a request hash and its response.

    Replaying the same key with the same payload returns the stored response.
    Replaying it with a *different* payload is a client error and returns 409 --
    silently treating it as the original request would hide a real bug
    (section 6.2).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="idempotency_records"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    route = models.CharField(max_length=200)
    key = models.CharField(max_length=200)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "common_idempotency_record"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "actor", "route", "key"],
                name="uniq_idempotency_scope",
            )
        ]
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self) -> str:
        return f"{self.route}:{self.key}"
