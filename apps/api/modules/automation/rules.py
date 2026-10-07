"""Rule administration and alert handling (CRM06).

Administrators work with the approved templates: they may change timing,
conditions, who is notified or assigned, limits and the explanation, and they
may enable or pause a rule. They cannot invent a trigger or an action outside
the closed schema in ``modules.automation.schemas``.

An edit never mutates the running rule. It writes a new version and retires the
old one, so jobs already queued under the old meaning are cancelled by the
engine rather than carried out under rules nobody approved.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from modules.automation import engine
from modules.automation.models import (
    Notification,
    NotificationState,
    RuleDefinition,
)
from modules.automation.schemas import RuleConfig
from modules.common.audit import AuditAction, record_audit
from modules.common.exceptions import NotAuthorized, NotFound, ValidationFailed
from modules.common.tenancy import current_workspace_id
from modules.identity.models import Membership, Role, User
from modules.identity.policy import has_permission
from modules.identity.services import assert_expected_version

#: The parts of a rule an administrator may change. Template and trigger are
#: fixed: changing them would make it a different rule.
EDITABLE_CONFIG = ("conditions", "delay", "actions", "limits")


def _require(membership: Membership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise NotAuthorized(f"This role may not perform {permission}.")


def current_rules() -> QuerySet[RuleDefinition]:
    """The live version of each rule: retired versions are soft-deleted."""
    return RuleDefinition.objects.filter(
        workspace_id=current_workspace_id(), deleted_at__isnull=True
    ).order_by("name")


def _get_current(rule_id: uuid.UUID, *, lock: bool = False) -> RuleDefinition:
    queryset = current_rules()
    if lock:
        queryset = queryset.select_for_update()
    rule = queryset.filter(pk=rule_id).first()
    if rule is None:
        raise NotFound("No current rule matches that identifier.")
    return rule


def config_of(rule: RuleDefinition) -> dict[str, Any]:
    return {
        "template": rule.template,
        "version": rule.rule_version,
        "trigger": rule.trigger,
        "conditions": rule.conditions,
        "delay": rule.delay or None,
        "actions": rule.actions,
        "limits": rule.limits or {},
    }


def _validated(config: dict[str, Any]) -> RuleConfig:
    try:
        return RuleConfig.model_validate(config)
    except PydanticValidationError as exc:
        field_errors: dict[str, list[str]] = {}
        for error in exc.errors():
            key = str(error["loc"][0]) if error["loc"] else "rule"
            field_errors.setdefault(key, []).append(error["msg"])
        raise ValidationFailed(
            "The rule configuration is not valid.", field_errors=field_errors
        ) from exc


def _stored(config: RuleConfig) -> dict[str, Any]:
    return {
        "conditions": [c.model_dump() for c in config.conditions],
        "actions": [a.model_dump(exclude_none=True) for a in config.actions],
        "delay": config.delay.model_dump(exclude_none=True) if config.delay else {},
        "limits": config.limits.model_dump(),
    }


@transaction.atomic
def update_rule(
    *,
    membership: Membership,
    actor: User,
    rule_id: uuid.UUID,
    expected_version: int,
    changes: dict[str, Any],
    explanation: str | None = None,
    request_id: str = "",
) -> RuleDefinition:
    """Write a new version of a rule and retire the current one."""
    _require(membership, "rule.manage")
    current = _get_current(rule_id, lock=True)
    assert_expected_version(current, expected_version)

    unknown = set(changes) - set(EDITABLE_CONFIG)
    if unknown:
        raise ValidationFailed(
            "Template and trigger cannot be changed; choose a different rule instead.",
            field_errors={name: ["Not editable."] for name in sorted(unknown)},
        )

    if not changes and explanation is None:
        raise ValidationFailed("Nothing to change: send at least one editable field.")

    proposed = config_of(current) | changes | {"version": current.rule_version + 1}
    config = _validated(proposed)
    if explanation is not None and not explanation.strip():
        raise ValidationFailed(
            "Every rule needs an explanation; it is shown with every alert it raises.",
            field_errors={"explanation": ["This field is required."]},
        )

    # Retire first: the unique key is on (workspace, template, rule_version),
    # and the retired row must stop being runnable before the new one exists.
    current.deleted_at = timezone.now()
    current.deleted_reason = f"Superseded by version {config.version}."
    current.version += 1
    current.save(update_fields=["deleted_at", "deleted_reason", "version", "updated_at"])

    successor = RuleDefinition.objects.create(
        workspace_id=current.workspace_id,
        template=current.template,
        name=current.name,
        rule_version=config.version,
        enabled=current.enabled,
        paused_at=current.paused_at,
        trigger=current.trigger,
        explanation=(explanation.strip() if explanation is not None else current.explanation),
        updated_by=actor,
        **_stored(config),
    )
    record_audit(
        action=AuditAction.UPDATE,
        entity_type="automation.RuleDefinition",
        entity_id=successor.id,
        actor=actor,
        changes={
            "template": current.template,
            "version": {"from": current.rule_version, "to": successor.rule_version},
            "changed": sorted(changes) + (["explanation"] if explanation is not None else []),
        },
        request_id=request_id,
    )
    return successor


@transaction.atomic
def set_rule_state(
    *,
    membership: Membership,
    actor: User,
    rule_id: uuid.UUID,
    expected_version: int,
    enabled: bool | None = None,
    paused: bool | None = None,
    request_id: str = "",
) -> RuleDefinition:
    """Enable, disable, pause or resume a rule.

    Pausing takes effect for work already queued: the engine re-checks the rule
    before every job and cancels it when the rule is no longer runnable.
    """
    _require(membership, "rule.manage")
    rule = _get_current(rule_id, lock=True)
    assert_expected_version(rule, expected_version)

    before = {"enabled": rule.enabled, "paused": rule.paused_at is not None}
    if enabled is not None:
        rule.enabled = enabled
    if paused is not None:
        rule.paused_at = timezone.now() if paused else None
    rule.updated_by = actor
    rule.version += 1
    rule.save()
    record_audit(
        action=AuditAction.UPDATE,
        entity_type="automation.RuleDefinition",
        entity_id=rule.id,
        actor=actor,
        changes={
            "before": before,
            "after": {"enabled": rule.enabled, "paused": rule.paused_at is not None},
        },
        request_id=request_id,
    )
    return rule


def simulate_rule(
    *,
    membership: Membership,
    rule_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
) -> dict[str, Any]:
    """Run a rule's conditions against one real record, without acting.

    Read-only and never contacts a provider (SVX-TECH-001 section 6.1).
    """
    _require(membership, "rule.manage")
    rule = _get_current(rule_id)
    context = engine.context_for(entity_type, entity_id)
    if context is None:
        raise NotFound(f"No {entity_type} matches that identifier.")
    result = engine.simulate(rule, context)
    result["delay"] = rule.delay or None
    result["explanation"] = rule.explanation
    return result


@transaction.atomic
def install_catalogue(*, membership: Membership, actor: User, request_id: str = "") -> int:
    """Install any catalogue templates this workspace lacks, all disabled.

    Existing rules, including edited ones, are never touched. Each new rule
    starts disabled so it is reviewed and simulated before it acts.
    """
    from modules.automation.management.commands.seed_rule_catalogue import CATALOGUE

    _require(membership, "rule.manage")
    workspace_id = current_workspace_id()
    present = set(
        RuleDefinition.objects.filter(workspace_id=workspace_id).values_list("template", flat=True)
    )
    installed = 0
    for entry in CATALOGUE:
        if entry["template"] in present:
            continue
        config = _validated({"template": entry["template"], "version": 1, **entry["config"]})
        rule = RuleDefinition.objects.create(
            workspace_id=workspace_id,
            template=entry["template"],
            name=entry["name"],
            rule_version=1,
            enabled=False,
            trigger=config.trigger,
            explanation=entry["explanation"],
            updated_by=actor,
            **_stored(config),
        )
        record_audit(
            action=AuditAction.CREATE,
            entity_type="automation.RuleDefinition",
            entity_id=rule.id,
            actor=actor,
            changes={"template": rule.template, "enabled": False},
            request_id=request_id,
        )
        installed += 1
    return installed


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

MANAGERS = {Role.OWNER, Role.SALES_MANAGER, Role.DELIVERY_MANAGER}


def visible_notifications(membership: Membership) -> QuerySet[Notification]:
    """A person sees alerts addressed to them; a manager also sees the team's."""
    queryset = Notification.objects.filter(
        workspace_id=current_workspace_id(), deleted_at__isnull=True
    ).order_by("-created_at")
    if membership.role in MANAGERS:
        return queryset
    return queryset.filter(recipient_id=membership.user_id)


@transaction.atomic
def acknowledge_notification(
    *, membership: Membership, actor: User, notification_id: uuid.UUID, request_id: str = ""
) -> Notification:
    """The recipient has seen it. Not the same as dealing with it."""
    notification = _get_notification(membership, notification_id)
    if notification.recipient_id != membership.user_id:
        raise NotAuthorized("Only the person an alert was sent to can acknowledge it.")
    if notification.resolved_at is None and notification.acknowledged_at is None:
        notification.acknowledged_at = timezone.now()
        notification.state = NotificationState.ACKNOWLEDGED
        notification.version += 1
        notification.save()
        record_audit(
            action=AuditAction.UPDATE,
            entity_type="automation.Notification",
            entity_id=notification.id,
            actor=actor,
            changes={"state": {"to": notification.state}},
            request_id=request_id,
        )
    return notification


@transaction.atomic
def resolve_notification(
    *,
    membership: Membership,
    actor: User,
    notification_id: uuid.UUID,
    note: str,
    request_id: str = "",
) -> Notification:
    """Close an alert with a note saying what was done.

    The recipient or a manager may resolve it. Resolving frees the dedupe key, so
    the same cause can alert again later if it recurs.
    """
    notification = _get_notification(membership, notification_id, lock=True)
    if notification.recipient_id != membership.user_id and membership.role not in MANAGERS:
        raise NotAuthorized("Only the recipient or a manager can resolve this alert.")
    if not note.strip():
        raise ValidationFailed(
            "Say what was done about it.", field_errors={"note": ["This field is required."]}
        )
    if notification.resolved_at is not None:
        return notification

    now = timezone.now()
    notification.resolved_at = now
    notification.acknowledged_at = notification.acknowledged_at or now
    notification.resolved_by = actor
    notification.resolution_note = note.strip()
    notification.state = NotificationState.RESOLVED
    notification.version += 1
    notification.save()
    record_audit(
        action=AuditAction.UPDATE,
        entity_type="automation.Notification",
        entity_id=notification.id,
        actor=actor,
        changes={"state": {"to": notification.state}},
        reason=note.strip()[:500],
        request_id=request_id,
    )
    return notification


def _get_notification(
    membership: Membership, notification_id: uuid.UUID, *, lock: bool = False
) -> Notification:
    queryset = visible_notifications(membership)
    if lock:
        queryset = queryset.select_for_update()
    notification = queryset.filter(pk=notification_id).first()
    if notification is None:
        raise NotFound("No alert matches that identifier.")
    return notification
