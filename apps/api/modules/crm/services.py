"""CRM state changes (CRM03, CRM04).

Every business transition lives here rather than in a view, a model signal or
the frontend. Services enforce permissions and invariants even when called by a
worker, a management command or a test (section 3.2).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.automation import outbox
from modules.common.audit import AuditAction, diff_fields, record_audit, snapshot
from modules.common.exceptions import (
    ConflictError,
    NotAuthorized,
    TransitionNotAllowed,
    ValidationFailed,
)
from modules.common.tenancy import assert_same_workspace, current_workspace_id
from modules.crm.models import (
    CLOSED_STAGES,
    Activity,
    ActivityKind,
    Contact,
    EvidenceType,
    Lead,
    LeadStatus,
    Opportunity,
    OpportunityStage,
    StageHistory,
)
from modules.crm.normalization import normalize_email, normalize_phone
from modules.identity.models import Membership, User
from modules.identity.policy import has_permission
from modules.identity.services import assert_expected_version

# Permitted stage movement (SVX-PRD-001 section 5.1). Backward movement is
# allowed between open stages -- a deal genuinely can fall back from Proposal to
# Discovery -- but a closed deal is terminal and must be reopened explicitly.
ALLOWED_STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    OpportunityStage.DISCOVERY: frozenset({OpportunityStage.QUALIFIED, OpportunityStage.LOST}),
    OpportunityStage.QUALIFIED: frozenset(
        {
            OpportunityStage.DISCOVERY,
            OpportunityStage.PROPOSAL,
            OpportunityStage.LOST,
        }
    ),
    OpportunityStage.PROPOSAL: frozenset(
        {
            OpportunityStage.QUALIFIED,
            OpportunityStage.NEGOTIATION,
            OpportunityStage.LOST,
        }
    ),
    OpportunityStage.NEGOTIATION: frozenset(
        {
            OpportunityStage.PROPOSAL,
            OpportunityStage.WON,
            OpportunityStage.LOST,
        }
    ),
    OpportunityStage.WON: frozenset(),
    OpportunityStage.LOST: frozenset(),
}

# Fields a stage must have before it can be entered.
STAGE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    OpportunityStage.PROPOSAL: ("scope_reference",),
    OpportunityStage.WON: (
        "accepted_scope_evidence",
        "commercial_reference",
        "delivery_owner_id",
    ),
}

LEAD_STATUS_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    LeadStatus.QUALIFIED: ("need", "fit"),
    LeadStatus.NURTURE: ("nurture_review_at",),
    LeadStatus.DISQUALIFIED: ("disqualified_reason",),
}


def _require(membership: Membership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise NotAuthorized(f"This role may not perform {permission}.")


def _assert_can_see(membership: Membership, record) -> None:
    """Record-level visibility (CRM01 acceptance).

    A representative sees their own records and those explicitly shared; a
    manager sees their team's; the owner sees the workspace. Changing a URL or
    request identifier must not reach somebody else's record -- which is why
    this is checked on retrieval, not only on listing.
    """
    from modules.identity.models import Role

    if membership.role in {Role.OWNER, Role.ADMIN, Role.SALES_MANAGER}:
        return
    owner_id = getattr(record, "owner_id", None)
    if owner_id is not None and owner_id != membership.user_id:
        # Deliberately a 403 rather than a 404 here: the caller already proved
        # the record is inside their own workspace by holding the context.
        raise NotAuthorized("This record belongs to another user.")


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


@transaction.atomic
def create_contact(
    *,
    membership: Membership,
    actor: User,
    display_name: str,
    email: str = "",
    phone: str = "",
    default_country: str = "",
    job_title: str = "",
    company=None,
    source: str = "",
    source_reference: str = "",
    request_id: str = "",
) -> Contact:
    _require(membership, "contact.manage")
    if not display_name.strip():
        raise ValidationFailed(
            "A contact needs a name.",
            field_errors={"display_name": ["This field is required."]},
        )

    normalized_email = normalize_email(email)
    phone_e164 = normalize_phone(phone, default_country=default_country)

    try:
        contact = Contact.objects.create(
            workspace_id=current_workspace_id(),
            display_name=display_name.strip(),
            company=company,
            email=email.strip(),
            normalized_email=normalized_email,
            phone=phone.strip(),
            phone_e164=phone_e164,
            job_title=job_title.strip(),
            source=source or "unknown",
            source_reference=source_reference,
        )
    except IntegrityError as exc:
        # The workspace-unique index on email or phone rejected this. That is a
        # duplicate the user can resolve, not a server fault, so it must not
        # surface as a 500 -- and the message names which value collided.
        duplicate = _describe_duplicate(normalized_email, phone_e164)
        raise ConflictError(
            f"A contact with that {duplicate} already exists in this workspace. "
            "Open the existing record instead of creating a second one.",
            code="duplicate_contact",
        ) from exc
    record_audit(
        action=AuditAction.CREATE,
        entity_type="crm.Contact",
        entity_id=contact.id,
        actor=actor,
        changes={"display_name": contact.display_name},
        request_id=request_id,
    )
    return contact


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------


@transaction.atomic
def create_lead(
    *,
    membership: Membership,
    actor: User,
    contact: Contact,
    owner: User | None = None,
    source: str = "unknown",
    source_reference: str = "",
    notes: str = "",
    import_row_key: str = "",
    request_id: str = "",
) -> Lead:
    _require(membership, "lead.manage")
    assert_same_workspace(contact)

    if owner is not None:
        _assert_assignable(owner)

    lead = Lead.objects.create(
        workspace_id=current_workspace_id(),
        contact=contact,
        owner=owner,
        source=source,
        source_reference=source_reference,
        notes=notes,
        status=LeadStatus.ASSIGNED if owner else LeadStatus.NEW,
        import_row_key=import_row_key,
    )
    record_audit(
        action=AuditAction.CREATE,
        entity_type="crm.Lead",
        entity_id=lead.id,
        actor=actor,
        changes={"contact_id": contact.id, "owner_id": owner.id if owner else None},
        request_id=request_id,
    )
    outbox.emit(
        event_type="lead.created",
        aggregate_type="lead",
        aggregate_id=lead.id,
        aggregate_version=lead.version,
        payload={"owner_id": str(owner.id) if owner else None},
    )
    return lead


@transaction.atomic
def change_lead_status(
    *,
    membership: Membership,
    actor: User,
    lead_id: uuid.UUID,
    new_status: str,
    expected_version: int,
    nurture_review_at=None,
    disqualified_reason: str = "",
    need: str = "",
    fit: str = "",
    request_id: str = "",
) -> Lead:
    """Move a lead's qualification state, validating what the state requires."""
    _require(membership, "lead.manage")

    lead = Lead.objects.select_for_update().get(
        pk=lead_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    _assert_can_see(membership, lead)
    assert_expected_version(lead, expected_version)

    if new_status not in LeadStatus.values:
        raise ValidationFailed("Unknown lead status.")

    before = snapshot(lead, ["status", "nurture_review_at", "disqualified_reason", "need", "fit"])

    lead.status = new_status
    if nurture_review_at is not None:
        lead.nurture_review_at = nurture_review_at
    if disqualified_reason:
        lead.disqualified_reason = disqualified_reason
    if need.strip():
        lead.need = need.strip()
    if fit.strip():
        lead.fit = fit.strip()
    if new_status == LeadStatus.QUALIFIED and lead.qualified_at is None:
        lead.qualified_at = timezone.now()

    for field_name in LEAD_STATUS_REQUIREMENTS.get(new_status, ()):
        if not getattr(lead, field_name, None):
            raise ValidationFailed(
                f"Moving a lead to {new_status} requires {field_name}.",
                field_errors={field_name: ["This field is required for this status."]},
            )

    lead.version += 1
    lead.save()

    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="crm.Lead",
        entity_id=lead.id,
        actor=actor,
        changes=diff_fields(
            before,
            snapshot(lead, ["status", "nurture_review_at", "disqualified_reason", "need", "fit"]),
        ),
        request_id=request_id,
    )
    return lead


@transaction.atomic
def assign_lead(
    *,
    membership: Membership,
    actor: User,
    lead_id: uuid.UUID,
    owner_id: uuid.UUID,
    expected_version: int,
    reason: str = "",
    request_id: str = "",
) -> Lead:
    """Transfer ownership, preserving the original activity author (CRM01)."""
    _require(membership, "lead.reassign")

    lead = Lead.objects.select_for_update().get(
        pk=lead_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(lead, expected_version)

    owner = _assert_assignable_by_id(owner_id)
    previous_owner_id = lead.owner_id

    lead.owner = owner
    if lead.status == LeadStatus.NEW:
        lead.status = LeadStatus.ASSIGNED
    lead.version += 1
    lead.save()

    record_audit(
        action=AuditAction.UPDATE,
        entity_type="crm.Lead",
        entity_id=lead.id,
        actor=actor,
        changes={"owner_id": {"from": previous_owner_id, "to": owner.id}},
        reason=reason,
        request_id=request_id,
    )
    outbox.emit(
        event_type="lead.assigned",
        aggregate_type="lead",
        aggregate_id=lead.id,
        aggregate_version=lead.version,
        payload={"owner_id": str(owner.id)},
    )
    return lead


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------


@transaction.atomic
def create_opportunity(
    *,
    membership: Membership,
    actor: User,
    contact: Contact,
    service: str,
    owner: User | None = None,
    lead: Lead | None = None,
    amount: Decimal | None = None,
    currency: str = "",
    expected_close_on=None,
    request_id: str = "",
) -> Opportunity:
    _require(membership, "opportunity.manage")
    assert_same_workspace(contact, lead)

    if amount is not None and not currency:
        raise ValidationFailed(
            "An amount requires a currency; totals must never mix currencies "
            "without an explicit basis.",
            field_errors={"currency": ["Required when an amount is given."]},
        )
    if owner is not None:
        _assert_assignable(owner)

    opportunity = Opportunity.objects.create(
        workspace_id=current_workspace_id(),
        contact=contact,
        lead=lead,
        service=service.strip(),
        owner=owner or actor,
        amount=amount,
        currency=currency.upper() if currency else "",
        expected_close_on=expected_close_on,
        stage=OpportunityStage.DISCOVERY,
        stage_entered_at=timezone.now(),
    )
    StageHistory.objects.create(
        id=uuid.uuid4(),
        workspace_id=opportunity.workspace_id,
        opportunity=opportunity,
        from_stage="",
        to_stage=OpportunityStage.DISCOVERY,
        actor=actor,
        changed_at=timezone.now(),
        reason="Opportunity created",
    )
    record_audit(
        action=AuditAction.CREATE,
        entity_type="crm.Opportunity",
        entity_id=opportunity.id,
        actor=actor,
        changes={"service": opportunity.service, "stage": opportunity.stage},
        request_id=request_id,
    )
    outbox.emit(
        event_type="opportunity.created",
        aggregate_type="opportunity",
        aggregate_id=opportunity.id,
        aggregate_version=opportunity.version,
    )
    return opportunity


@transaction.atomic
def transition_opportunity(
    *,
    membership: Membership,
    actor: User,
    opportunity_id: uuid.UUID,
    target_stage: str,
    expected_version: int,
    reason: str = "",
    scope_reference: str = "",
    lost_reason: str = "",
    request_id: str = "",
) -> Opportunity:
    """Move a deal to a new stage.

    Winning is not done here. ``won`` requires creating a client and an
    onboarding project in the same transaction (CRM07), so it goes through the
    dedicated conversion endpoint in Stage 2 rather than a generic transition.
    """
    _require(membership, "opportunity.transition")

    opportunity = Opportunity.objects.select_for_update().get(
        pk=opportunity_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    _assert_can_see(membership, opportunity)
    assert_expected_version(opportunity, expected_version)

    if target_stage not in OpportunityStage.values:
        raise ValidationFailed("Unknown stage.")

    if target_stage == OpportunityStage.WON:
        raise TransitionNotAllowed(
            "Closing as won goes through the conversion endpoint, which creates "
            "the client and onboarding project in the same transaction.",
            code="use_convert_endpoint",
        )

    current = opportunity.stage
    if target_stage == current:
        return opportunity

    allowed = ALLOWED_STAGE_TRANSITIONS.get(current, frozenset())
    if target_stage not in allowed:
        raise TransitionNotAllowed(
            f"A deal cannot move from {current} to {target_stage}.",
            extra={"from_stage": current, "allowed": sorted(allowed)},
        )

    before = snapshot(opportunity, ["stage", "scope_reference", "lost_reason"])

    if scope_reference:
        opportunity.scope_reference = scope_reference
    if lost_reason:
        opportunity.lost_reason = lost_reason

    for field_name in STAGE_REQUIREMENTS.get(target_stage, ()):
        if not getattr(opportunity, field_name, None):
            raise ValidationFailed(
                f"Moving to {target_stage} requires {field_name}.",
                field_errors={field_name: ["This field is required for this stage."]},
            )
    if target_stage == OpportunityStage.LOST and not opportunity.lost_reason:
        raise ValidationFailed(
            "Closing a deal as lost requires a reason.",
            field_errors={"lost_reason": ["This field is required."]},
        )

    opportunity.stage = target_stage
    opportunity.stage_entered_at = timezone.now()
    if target_stage in CLOSED_STAGES:
        opportunity.closed_at = timezone.now()
    opportunity.version += 1
    opportunity.save()

    StageHistory.objects.create(
        id=uuid.uuid4(),
        workspace_id=opportunity.workspace_id,
        opportunity=opportunity,
        from_stage=current,
        to_stage=target_stage,
        actor=actor,
        changed_at=timezone.now(),
        reason=reason,
    )
    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="crm.Opportunity",
        entity_id=opportunity.id,
        actor=actor,
        changes=diff_fields(
            before, snapshot(opportunity, ["stage", "scope_reference", "lost_reason"])
        ),
        reason=reason,
        request_id=request_id,
    )
    outbox.emit(
        event_type="opportunity.stage_changed",
        aggregate_type="opportunity",
        aggregate_id=opportunity.id,
        aggregate_version=opportunity.version,
        payload={"from_stage": current, "to_stage": target_stage},
    )

    if target_stage in CLOSED_STAGES:
        # A closed deal must not keep generating follow-up reminders.
        outbox_prefix = f"opportunity:{opportunity.id}"
        outbox.cancel_jobs_for(
            dedupe_prefix=outbox_prefix,
            reason="Opportunity closed",
            workspace_id=opportunity.workspace_id,
        )
    return opportunity


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@transaction.atomic
def record_activity(
    *,
    membership: Membership,
    actor: User,
    contact: Contact,
    kind: str,
    occurred_at,
    outcome: str = "",
    opportunity: Opportunity | None = None,
    lead: Lead | None = None,
    source_url: str = "",
    provider_reference: str = "",
    follow_up_title: str = "",
    follow_up_due_at=None,
    request_id: str = "",
) -> Activity:
    """Record an interaction, and optionally schedule the follow-up it agreed.

    The follow-up is an ordinary task created through the task service in the
    same transaction, so the activity and its next action commit together.

    Evidence labelling is decided here, not by the caller. A manually entered
    event is always self-reported, whatever the client sends -- otherwise a
    crafted request could make a typed-in claim appear provider-confirmed
    (CRM04 acceptance).
    """
    _require(membership, "activity.record")
    assert_same_workspace(contact, opportunity, lead)

    if kind not in ActivityKind.values:
        raise ValidationFailed("Unknown activity kind.")

    now = timezone.now()
    if occurred_at > now:
        raise ValidationFailed(
            "An activity cannot be recorded as having happened in the future.",
            field_errors={"occurred_at": ["Must not be in the future."]},
        )

    activity = Activity.objects.create(
        workspace_id=current_workspace_id(),
        contact=contact,
        opportunity=opportunity,
        lead=lead,
        kind=kind,
        outcome=outcome,
        occurred_at=occurred_at,
        # Recorded-at is server time. Keeping it separate from occurred-at is
        # what shows a week of calls entered in one Friday burst.
        recorded_at=now,
        author=actor,
        evidence_type=EvidenceType.SELF_REPORTED,
        provider_reference="",
        source_url=source_url,
    )

    if follow_up_title.strip() or follow_up_due_at is not None:
        if not follow_up_title.strip() or follow_up_due_at is None:
            raise ValidationFailed(
                "A follow-up needs both a title and a due time.",
                field_errors={
                    "follow_up_title": ["Required with a due time."],
                    "follow_up_due_at": ["Required with a title."],
                },
            )
        from modules.work import services as work_services

        activity.follow_up_task = work_services.create_task(
            membership=membership,
            actor=actor,
            title=follow_up_title,
            due_at=follow_up_due_at,
            contact=contact,
            opportunity=opportunity,
            lead=lead,
            request_id=request_id,
        )
        activity.save(update_fields=["follow_up_task", "updated_at"])

    if opportunity is not None:
        opportunity.last_activity_at = occurred_at
        opportunity.save(update_fields=["last_activity_at", "updated_at"])

    record_audit(
        action=AuditAction.CREATE,
        entity_type="crm.Activity",
        entity_id=activity.id,
        actor=actor,
        changes={"kind": kind, "occurred_at": occurred_at},
        request_id=request_id,
    )
    outbox.emit(
        event_type="activity.recorded",
        aggregate_type="opportunity" if opportunity else "lead",
        aggregate_id=opportunity.id if opportunity else (lead.id if lead else contact.id),
        payload={"activity_id": str(activity.id), "kind": kind},
    )
    return activity


@transaction.atomic
def correct_activity(
    *,
    membership: Membership,
    actor: User,
    activity_id: uuid.UUID,
    outcome: str,
    reason: str,
    request_id: str = "",
) -> Activity:
    """Append a correction rather than editing the original (Journey D).

    The original stays readable and the correction links back to it, so an
    edited outcome leaves a trail instead of quietly replacing what was said.
    """
    _require(membership, "activity.record")
    if not reason.strip():
        raise ValidationFailed(
            "A correction must record why it was made.",
            field_errors={"reason": ["This field is required."]},
        )

    original = Activity.objects.get(
        pk=activity_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )

    correction = Activity.objects.create(
        workspace_id=original.workspace_id,
        contact=original.contact,
        opportunity=original.opportunity,
        lead=original.lead,
        kind=original.kind,
        outcome=outcome,
        occurred_at=original.occurred_at,
        recorded_at=timezone.now(),
        author=actor,
        evidence_type=EvidenceType.SELF_REPORTED,
        source_url=original.source_url,
        corrects=original,
    )
    record_audit(
        action=AuditAction.CORRECTION,
        entity_type="crm.Activity",
        entity_id=correction.id,
        actor=actor,
        changes={"corrects": str(original.id), "outcome": {"to": outcome}},
        reason=reason,
        request_id=request_id,
    )
    return correction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_assignable(user: User) -> Membership:
    return _assert_assignable_by_id(user.id)


def _assert_assignable_by_id(user_id: uuid.UUID) -> User:
    """Reject assignment to a suspended or absent member.

    Checked at assignment time rather than trusting a roster the client sent.
    """
    membership = (
        Membership.objects.select_related("user")
        .filter(workspace_id=current_workspace_id(), user_id=user_id)
        .first()
    )
    if membership is None:
        raise ValidationFailed(
            "That person is not a member of this workspace.",
            field_errors={"owner_id": ["Not a workspace member."]},
        )
    if not membership.can_receive_assignment:
        raise ValidationFailed(
            "That person is suspended or unavailable and cannot receive new work.",
            field_errors={"owner_id": ["Not available for assignment."]},
        )
    return membership.user


def _describe_duplicate(normalized_email: str, phone_e164: str) -> str:
    """Name the field that collided, so the message is actionable.

    Both may be set, in which case the email is the more likely cause and the
    more recognisable one to a user scanning their contact list.
    """
    if normalized_email:
        return "email address"
    if phone_e164:
        return "phone number"
    return "identifying detail"


# ---------------------------------------------------------------------------
# Editing (CRM03: PATCH /leads/{id}, and the contact and deal behind it)
#
# Each editor takes an allowlist of plain fields and an expected version, so a
# stale form cannot overwrite a colleague's change. State, ownership and stage
# are not editable here: they move only through their own transitions, which
# carry their own evidence requirements.
# ---------------------------------------------------------------------------

CONTACT_EDITABLE = frozenset({"display_name", "email", "phone", "job_title", "source_reference"})
LEAD_EDITABLE = frozenset({"source", "source_reference", "notes", "need", "fit"})
OPPORTUNITY_EDITABLE = frozenset(
    {
        "service",
        "amount",
        "currency",
        "expected_close_on",
        "scope_reference",
        "commercial_reference",
    }
)


def _apply_changes(instance, changes: dict, editable: frozenset[str]) -> dict:
    unknown = set(changes) - editable
    if unknown:
        raise ValidationFailed(
            "These fields cannot be edited here.",
            field_errors={name: ["Not editable."] for name in sorted(unknown)},
        )
    before = snapshot(instance, sorted(changes))
    for name, value in changes.items():
        setattr(instance, name, value.strip() if isinstance(value, str) else value)
    return before


@transaction.atomic
def update_contact(
    *,
    membership: Membership,
    actor: User,
    contact_id: uuid.UUID,
    expected_version: int,
    changes: dict,
    default_country: str = "",
    request_id: str = "",
) -> Contact:
    """Correct a person's details, renormalising email and phone.

    The duplicate rule is the same as on creation: an email or phone already
    held by another contact in the workspace is refused, naming the value,
    rather than creating two records for one person.
    """
    _require(membership, "contact.manage")
    contact = Contact.objects.select_for_update().get(
        pk=contact_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(contact, expected_version)
    if "display_name" in changes and not str(changes["display_name"]).strip():
        raise ValidationFailed(
            "A contact needs a name.",
            field_errors={"display_name": ["This field is required."]},
        )

    before = _apply_changes(contact, changes, CONTACT_EDITABLE)
    if "email" in changes:
        contact.normalized_email = normalize_email(contact.email)
    if "phone" in changes:
        contact.phone_e164 = normalize_phone(contact.phone, default_country=default_country)
    contact.version += 1
    try:
        with transaction.atomic():
            contact.save()
    except IntegrityError as exc:
        duplicate = _describe_duplicate(contact.normalized_email, contact.phone_e164)
        raise ConflictError(
            f"Another contact in this workspace already has that {duplicate}.",
            code="duplicate_contact",
        ) from exc

    record_audit(
        action=AuditAction.UPDATE,
        entity_type="crm.Contact",
        entity_id=contact.id,
        actor=actor,
        changes=diff_fields(before, snapshot(contact, sorted(changes))),
        request_id=request_id,
    )
    return contact


@transaction.atomic
def update_lead(
    *,
    membership: Membership,
    actor: User,
    lead_id: uuid.UUID,
    expected_version: int,
    changes: dict,
    request_id: str = "",
) -> Lead:
    """Edit a lead's descriptive fields. Status and owner have their own actions."""
    _require(membership, "lead.manage")
    lead = Lead.objects.select_for_update().get(
        pk=lead_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    _assert_can_see(membership, lead)
    assert_expected_version(lead, expected_version)

    # A qualified lead must keep the reasons it was qualified.
    if lead.status == LeadStatus.QUALIFIED:
        for name in ("need", "fit"):
            if name in changes and not str(changes[name]).strip():
                raise ValidationFailed(
                    f"A qualified lead must keep its {name}.",
                    field_errors={name: ["Required while the lead is qualified."]},
                )

    before = _apply_changes(lead, changes, LEAD_EDITABLE)
    lead.version += 1
    lead.save()
    record_audit(
        action=AuditAction.UPDATE,
        entity_type="crm.Lead",
        entity_id=lead.id,
        actor=actor,
        changes=diff_fields(before, snapshot(lead, sorted(changes))),
        request_id=request_id,
    )
    return lead


@transaction.atomic
def update_opportunity(
    *,
    membership: Membership,
    actor: User,
    opportunity_id: uuid.UUID,
    expected_version: int,
    changes: dict,
    request_id: str = "",
) -> Opportunity:
    """Edit an open deal: what is sold, its value, currency and expected close.

    A closed deal is read-only. Its won value is the contract figure the
    reports and the handover were built on; changing it afterwards would rewrite
    history rather than record a change.

    The value rule matches creation: an amount needs a currency, and clearing
    the amount returns the value to unknown, never to zero.
    """
    _require(membership, "opportunity.manage")
    opportunity = Opportunity.objects.select_for_update().get(
        pk=opportunity_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    _assert_can_see(membership, opportunity)
    assert_expected_version(opportunity, expected_version)

    if opportunity.stage in CLOSED_STAGES:
        raise TransitionNotAllowed(
            "A closed deal cannot be edited.",
            extra={"stage": opportunity.stage},
        )
    if "service" in changes and not str(changes["service"]).strip():
        raise ValidationFailed(
            "Say what is being sold.", field_errors={"service": ["This field is required."]}
        )

    before = _apply_changes(opportunity, changes, OPPORTUNITY_EDITABLE)
    opportunity.currency = (opportunity.currency or "").upper()
    if opportunity.amount is not None and not opportunity.currency:
        raise ValidationFailed(
            "An amount needs a currency. A total that mixes currencies is worse than no total.",
            field_errors={"currency": ["Required with an amount."]},
        )
    if opportunity.amount is not None and opportunity.amount < 0:
        raise ValidationFailed(
            "A deal value cannot be negative.", field_errors={"amount": ["Must be zero or more."]}
        )
    if opportunity.amount is None:
        opportunity.currency = ""

    opportunity.version += 1
    opportunity.save()
    record_audit(
        action=AuditAction.UPDATE,
        entity_type="crm.Opportunity",
        entity_id=opportunity.id,
        actor=actor,
        changes=diff_fields(before, snapshot(opportunity, sorted(changes))),
        request_id=request_id,
    )
    return opportunity


# ---------------------------------------------------------------------------
# Duplicates and merging (CRM02)
# ---------------------------------------------------------------------------

#: Survivor fields filled from the duplicate only when the survivor has none.
MERGE_FILL_FIELDS = ("email", "phone", "job_title", "company_id")


def suggest_duplicates(*, membership: Membership, contact_id: uuid.UUID) -> list[dict]:
    """Contacts that may be the same person, for a human to review.

    Exact email or phone matches cannot exist between live contacts (the
    database forbids them), so what remains are name similarities: suggestions
    only, never merged automatically.
    """
    from modules.crm.normalization import find_duplicates

    _require(membership, "contact.view")
    contact = Contact.objects.get(
        pk=contact_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    matches = find_duplicates(
        workspace_id=contact.workspace_id,
        normalized_email=contact.normalized_email,
        phone_e164=contact.phone_e164,
        display_name=contact.display_name,
    )
    ids = [m.contact_id for m in matches if m.contact_id != contact.id]
    candidates = {
        c.id: c
        for c in Contact.objects.filter(
            pk__in=ids, workspace_id=contact.workspace_id, deleted_at__isnull=True
        )
    }
    return [
        {
            "id": match.contact_id,
            "display_name": candidates[match.contact_id].display_name,
            "email": candidates[match.contact_id].email,
            "phone": candidates[match.contact_id].phone,
            "version": candidates[match.contact_id].version,
            "reason": match.reason,
            "is_definitive": match.is_definitive,
        }
        for match in matches
        if match.contact_id in candidates
    ]


@transaction.atomic
def merge_contacts(
    *,
    membership: Membership,
    actor: User,
    survivor_id: uuid.UUID,
    duplicate_id: uuid.UUID,
    survivor_version: int,
    duplicate_version: int,
    reason: str,
    request_id: str = "",
) -> Contact:
    """Fold a duplicate contact into the one that survives (CRM02).

    Everything that pointed at the duplicate - leads, deals, activities, tasks,
    clients - now points at the survivor, so the history reads as one person.
    The duplicate is not erased: it is soft-deleted with ``merged_into`` set and
    its own source reference intact, and the survivor records where it came
    from. Both versions are checked so nobody merges a record that just changed.
    """
    _require(membership, "contact.merge")
    # The survivor id arrives from the URL as text; compare like with like.
    survivor_id, duplicate_id = uuid.UUID(str(survivor_id)), uuid.UUID(str(duplicate_id))
    if not reason.strip():
        raise ValidationFailed(
            "Say why these are the same person.", field_errors={"reason": ["Required."]}
        )
    if survivor_id == duplicate_id:
        raise ValidationFailed("A contact cannot be merged into itself.")

    workspace_id = current_workspace_id()
    locked = {
        c.id: c
        for c in Contact.objects.select_for_update().filter(
            pk__in=[survivor_id, duplicate_id], workspace_id=workspace_id, deleted_at__isnull=True
        )
    }
    survivor, duplicate = locked.get(survivor_id), locked.get(duplicate_id)
    if survivor is None or duplicate is None:
        raise ValidationFailed("Both contacts must exist and not already be merged.")
    assert_expected_version(survivor, survivor_version)
    assert_expected_version(duplicate, duplicate_version)

    moved: dict[str, int] = {}
    for relation in Contact._meta.related_objects:
        if relation.related_model is Contact:
            continue
        count = relation.related_model.objects.filter(
            **{relation.field.name: duplicate, "workspace_id": workspace_id}
        ).update(**{relation.field.name: survivor})
        if count:
            moved[relation.related_model._meta.label] = count

    # Retire the duplicate first: the unique email and phone keys apply only to
    # live contacts, and the survivor may now take over the duplicate's values.
    duplicate.merged_into = survivor
    duplicate.deleted_at = timezone.now()
    duplicate.deleted_reason = f"Merged into {survivor.id}: {reason.strip()}"
    duplicate.version += 1
    duplicate.save()

    filled = []
    for field in MERGE_FILL_FIELDS:
        if not getattr(survivor, field) and getattr(duplicate, field):
            setattr(survivor, field, getattr(duplicate, field))
            filled.append(field)
    if "email" in filled:
        survivor.normalized_email = duplicate.normalized_email
    if "phone" in filled:
        survivor.phone_e164 = duplicate.phone_e164

    provenance = dict(survivor.raw_import_values or {})
    provenance.setdefault("merged_from", []).append(
        {
            "contact_id": str(duplicate.id),
            "display_name": duplicate.display_name,
            "source": duplicate.source,
            "source_reference": duplicate.source_reference,
            "merged_at": timezone.now().isoformat(),
            "merged_by": str(actor.id),
        }
    )
    survivor.raw_import_values = provenance
    survivor.version += 1
    survivor.save()

    record_audit(
        action=AuditAction.UPDATE,
        entity_type="crm.Contact",
        entity_id=survivor.id,
        actor=actor,
        changes={"merged_from": str(duplicate.id), "moved": moved, "filled": filled},
        reason=reason.strip(),
        request_id=request_id,
    )
    record_audit(
        action=AuditAction.UPDATE,
        entity_type="crm.Contact",
        entity_id=duplicate.id,
        actor=actor,
        changes={"merged_into": str(survivor.id)},
        reason=reason.strip(),
        request_id=request_id,
    )
    return survivor
