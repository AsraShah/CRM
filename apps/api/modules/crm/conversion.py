"""Won deal to accepted delivery (CRM07, Journey C).

The whole point of this module is that **one transaction** records the won
state, links or creates the client, creates the uniquely keyed onboarding
project with its template tasks, and writes the audit and outbox records. Either
all of that happened, or none of it did.

Half a conversion is the worst possible outcome: a deal marked won with no
client, or a client with no project, leaves the business believing work is
under way when nobody has been told to start it.

Two further rules from the brief:

* **Retrying creates no duplicate.** The conversion key is unique, so a retry
  after a timeout returns the existing result.
* **Acceptance is separate from creation.** Creating the handover does not mean
  delivery agreed to it. The delivery manager accepts it, or returns it naming
  what is missing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.automation import outbox
from modules.common.audit import AuditAction, record_audit
from modules.common.exceptions import (
    ConflictError,
    NotAuthorized,
    TransitionNotAllowed,
    ValidationFailed,
)
from modules.common.tenancy import current_workspace_id
from modules.crm.models import (
    Client,
    ClientStatus,
    Opportunity,
    OpportunityStage,
    StageHistory,
)
from modules.identity.models import Membership, Role, User
from modules.identity.policy import has_permission
from modules.identity.services import assert_expected_version
from modules.work.models import Milestone, MilestoneStatus, Project, ProjectStatus

#: Onboarding templates. Release 1 ships one; the shape is what matters.
ONBOARDING_TEMPLATES: dict[str, dict] = {
    "standard_agency_onboarding": {
        "name": "Client onboarding",
        "milestones": [
            {"name": "Kick-off call booked", "offset_days": 3},
            {"name": "Access and assets received", "offset_days": 7},
            {"name": "Delivery plan agreed", "offset_days": 10},
            {"name": "First deliverable accepted", "offset_days": 30},
        ],
    }
}


@dataclass(slots=True)
class ConversionResult:
    opportunity: Opportunity
    client: Client
    project: Project
    #: True when this call found an existing conversion rather than creating one.
    was_existing: bool


def _require(membership: Membership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise NotAuthorized(f"This role may not perform {permission}.")


@transaction.atomic
def convert_won_deal(
    *,
    membership: Membership,
    actor: User,
    opportunity_id: uuid.UUID,
    expected_version: int,
    accepted_scope_evidence: str,
    commercial_reference: str,
    delivery_owner_id: uuid.UUID | None,
    exclusions: str = "",
    promised_start_on: date | None = None,
    promised_end_on: date | None = None,
    template_key: str = "standard_agency_onboarding",
    request_id: str = "",
) -> ConversionResult:
    """Close a deal as won and stand up its delivery, atomically."""
    _require(membership, "opportunity.convert")

    workspace_id = current_workspace_id()

    # Lock the opportunity row for the whole transaction. Two people clicking
    # "won" at the same moment must not both proceed.
    opportunity = Opportunity.objects.select_for_update().get(
        pk=opportunity_id, workspace_id=workspace_id, deleted_at__isnull=True
    )

    conversion_key = f"conversion:{opportunity.id}"

    # Idempotent short-circuit. A retry after a client timeout finds the work
    # already done and returns it, rather than building a second client.
    if opportunity.stage == OpportunityStage.WON:
        existing_project = Project.objects.filter(
            workspace_id=workspace_id, conversion_key=conversion_key
        ).first()
        existing_client = getattr(opportunity, "converted_client", None)
        if existing_project is not None and existing_client is not None:
            return ConversionResult(
                opportunity=opportunity,
                client=existing_client,
                project=existing_project,
                was_existing=True,
            )
        # Won, but without its delivery records: a previous attempt failed
        # partway. Surface it rather than silently patching over it.
        raise ConflictError(
            "This deal is marked won but its client or onboarding project is "
            "missing. A previous conversion did not complete. Escalate rather "
            "than retrying.",
            code="conversion_incomplete",
        )

    assert_expected_version(opportunity, expected_version)

    if opportunity.stage != OpportunityStage.NEGOTIATION:
        raise TransitionNotAllowed(
            "Only a deal in negotiation can be closed as won.",
            extra={"from_stage": opportunity.stage},
        )

    # --- Required evidence (CRM03, CRM07) ---------------------------------
    field_errors: dict[str, list[str]] = {}
    if not accepted_scope_evidence.strip():
        field_errors["accepted_scope_evidence"] = ["Record what scope the client accepted."]
    if not commercial_reference.strip():
        field_errors["commercial_reference"] = ["Record the commercial decision reference."]
    if field_errors:
        raise ValidationFailed(
            "Closing a deal as won requires accepted scope evidence and a "
            "recorded commercial decision. It does not mean payment has been "
            "received.",
            field_errors=field_errors,
        )

    delivery_owner = _resolve_delivery_owner(delivery_owner_id, workspace_id)

    # --- Won state ---------------------------------------------------------
    previous_stage = opportunity.stage
    now = timezone.now()

    opportunity.stage = OpportunityStage.WON
    opportunity.accepted_scope_evidence = accepted_scope_evidence.strip()
    opportunity.commercial_reference = commercial_reference.strip()
    opportunity.delivery_owner = delivery_owner
    opportunity.stage_entered_at = now
    opportunity.closed_at = now
    opportunity.version += 1
    opportunity.save()

    StageHistory.objects.create(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        opportunity=opportunity,
        from_stage=previous_stage,
        to_stage=OpportunityStage.WON,
        actor=actor,
        changed_at=now,
        reason="Converted to client",
    )

    # --- Client ------------------------------------------------------------
    # Reuse an existing client for this contact where one exists: a repeat
    # customer is not a new client record.
    client = (
        Client.objects.filter(
            workspace_id=workspace_id,
            contact=opportunity.contact,
            deleted_at__isnull=True,
        )
        .order_by("created_at")
        .first()
    )
    if client is None:
        client = Client.objects.create(
            workspace_id=workspace_id,
            contact=opportunity.contact,
            company=opportunity.contact.company,
            display_name=(
                opportunity.contact.company.name
                if opportunity.contact.company
                else opportunity.contact.display_name
            ),
            originating_opportunity=opportunity,
            delivery_owner=delivery_owner,
            # Not accepted. Delivery has not agreed to anything yet.
            status=ClientStatus.PENDING_HANDOVER,
            accepted_scope=accepted_scope_evidence.strip(),
            exclusions=exclusions.strip(),
            commercial_reference=commercial_reference.strip(),
            promised_start_on=promised_start_on,
            promised_end_on=promised_end_on,
        )
    elif client.originating_opportunity_id is None:
        client.originating_opportunity = opportunity
        client.save(update_fields=["originating_opportunity", "updated_at"])

    # --- Onboarding project ------------------------------------------------
    template = ONBOARDING_TEMPLATES.get(template_key)
    if template is None:
        raise ValidationFailed(
            f"Unknown onboarding template {template_key!r}.",
            field_errors={"template_key": ["Unknown template."]},
        )

    try:
        project = Project.objects.create(
            workspace_id=workspace_id,
            client=client,
            originating_opportunity=opportunity,
            name=f"{template['name']}: {client.display_name}",
            status=ProjectStatus.PLANNED,
            owner=delivery_owner,
            template_key=template_key,
            conversion_key=conversion_key,
            starts_on=promised_start_on,
            due_on=promised_end_on,
        )
    except IntegrityError as exc:
        # The unique conversion key rejected a concurrent duplicate. The other
        # transaction won; this one must not create a second project.
        raise ConflictError(
            "This deal is already being converted. Reload to see the result.",
            code="conversion_in_progress",
        ) from exc

    _create_template_milestones(project, template, promised_start_on or now.date())

    # --- Evidence and downstream work --------------------------------------
    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="crm.Opportunity",
        entity_id=opportunity.id,
        actor=actor,
        changes={
            "stage": {"from": previous_stage, "to": OpportunityStage.WON},
            "client_id": str(client.id),
            "project_id": str(project.id),
        },
        reason="Won and converted",
        request_id=request_id,
    )
    outbox.emit(
        event_type="opportunity.stage_changed",
        aggregate_type="opportunity",
        aggregate_id=opportunity.id,
        aggregate_version=opportunity.version,
        payload={"from_stage": previous_stage, "to_stage": OpportunityStage.WON},
    )
    outbox.emit(
        event_type="client.handover_requested",
        aggregate_type="client",
        aggregate_id=client.id,
        aggregate_version=client.version,
        payload={"project_id": str(project.id)},
    )

    # A won deal stops generating sales follow-up reminders.
    outbox.cancel_jobs_for(
        dedupe_prefix=f"opportunity:{opportunity.id}",
        reason="Deal converted",
        workspace_id=workspace_id,
    )

    return ConversionResult(
        opportunity=opportunity, client=client, project=project, was_existing=False
    )


def _resolve_delivery_owner(delivery_owner_id, workspace_id) -> User:
    """A handover with no named delivery owner is not a handover.

    Missing ownership is precisely what puts a conversion into the exception
    queue, so it is rejected here with a message that says what to do.
    """
    if delivery_owner_id is None:
        raise ValidationFailed(
            "A handover needs a named delivery owner. Without one, nobody is "
            "responsible for starting the work.",
            field_errors={"delivery_owner_id": ["This field is required."]},
            code="missing_delivery_owner",
        )

    membership = (
        Membership.objects.select_related("user")
        .filter(workspace_id=workspace_id, user_id=delivery_owner_id)
        .first()
    )
    if membership is None or not membership.can_receive_assignment:
        raise ValidationFailed(
            "That person cannot take delivery ownership: they are not an "
            "active, available member of this workspace.",
            field_errors={"delivery_owner_id": ["Not available."]},
        )
    if membership.role not in {
        Role.OWNER,
        Role.DELIVERY_MANAGER,
        Role.DELIVERY_EMPLOYEE,
    }:
        raise ValidationFailed(
            "Delivery ownership belongs to a delivery role.",
            field_errors={"delivery_owner_id": ["Not a delivery role."]},
        )
    return membership.user


def _create_template_milestones(project: Project, template: dict, start: date) -> None:
    """Instantiate the template's milestones in sequence.

    Due dates are calendar-day offsets from the promised start, not working-day
    offsets: these are commitments to a client, who does not observe our working
    calendar.
    """
    from datetime import timedelta

    for index, entry in enumerate(template["milestones"]):
        Milestone.objects.create(
            workspace_id=project.workspace_id,
            project=project,
            name=entry["name"],
            sequence=index,
            status=MilestoneStatus.PLANNED,
            owner=project.owner,
            due_on=start + timedelta(days=entry["offset_days"]),
        )


@transaction.atomic
def accept_handover(
    *,
    membership: Membership,
    actor: User,
    client_id: uuid.UUID,
    expected_version: int,
    note: str = "",
    request_id: str = "",
) -> Client:
    """Delivery accepts the handover.

    Separate from project creation and visible to sales (Journey C step 3).
    """
    _require(membership, "handover.accept")

    client = Client.objects.select_for_update().get(
        pk=client_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(client, expected_version)

    if client.status != ClientStatus.PENDING_HANDOVER:
        raise TransitionNotAllowed(
            "Only a handover that is still pending can be accepted.",
            extra={"status": client.status},
        )

    client.status = ClientStatus.ACCEPTED
    client.handover_accepted_at = timezone.now()
    client.handover_accepted_by = actor
    client.handover_returned_at = None
    client.handover_returned_reason = ""
    client.version += 1
    client.save()

    Project.objects.filter(
        workspace_id=client.workspace_id, client=client, status=ProjectStatus.PLANNED
    ).update(status=ProjectStatus.IN_PROGRESS, updated_at=timezone.now())

    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="crm.Client",
        entity_id=client.id,
        actor=actor,
        changes={"status": {"to": ClientStatus.ACCEPTED}},
        reason=note,
        request_id=request_id,
    )
    outbox.emit(
        event_type="client.handover_accepted",
        aggregate_type="client",
        aggregate_id=client.id,
        aggregate_version=client.version,
    )
    return client


@transaction.atomic
def return_handover(
    *,
    membership: Membership,
    actor: User,
    client_id: uuid.UUID,
    expected_version: int,
    missing_information: str,
    request_id: str = "",
) -> Client:
    """Delivery returns the handover, naming what is missing.

    "Return with specific missing information" is the requirement (CRM07). A
    bare rejection would leave sales guessing, so the reason is mandatory.
    """
    _require(membership, "handover.accept")

    if not missing_information.strip():
        raise ValidationFailed(
            "Say specifically what is missing, so sales can supply it.",
            field_errors={"missing_information": ["This field is required."]},
        )

    client = Client.objects.select_for_update().get(
        pk=client_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(client, expected_version)

    if client.status != ClientStatus.PENDING_HANDOVER:
        raise TransitionNotAllowed(
            "Only a pending handover can be returned.", extra={"status": client.status}
        )

    client.handover_returned_at = timezone.now()
    client.handover_returned_reason = missing_information.strip()
    client.version += 1
    client.save()

    record_audit(
        action=AuditAction.UPDATE,
        entity_type="crm.Client",
        entity_id=client.id,
        actor=actor,
        changes={"handover_returned": True},
        reason=missing_information.strip(),
        request_id=request_id,
    )
    outbox.emit(
        event_type="client.handover_returned",
        aggregate_type="client",
        aggregate_id=client.id,
        aggregate_version=client.version,
        payload={"reason": missing_information.strip()[:200]},
    )
    return client
