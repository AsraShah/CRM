"""AI drafting service (CRM12, SVX-TECH-001 section 10).

The order of operations is the safety property:

    authorise records -> build fenced prompt -> reserve budget -> dispatch
                      -> validate output -> settle -> store draft

Nothing is sent before it is authorised. Nothing is dispatched before the spend
is reserved. Nothing is shown to a user before it validates against the schema
and every citation is checked.

Core functions continue when AI is disabled, unavailable or over budget. No
employee is blocked from basic work by this module (CRM12).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from modules.ai import budget
from modules.ai.contracts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    OutputRejected,
    build_source_block,
    validate_output,
)
from modules.ai.models import AIDraft, DraftPurpose
from modules.common.audit import AuditAction, record_audit
from modules.common.exceptions import NotAuthorized, ValidationFailed
from modules.common.tenancy import current_workspace_id, workspace_context
from modules.crm.models import Activity, Opportunity
from modules.identity.models import Membership, Role, User
from modules.identity.policy import has_permission

logger = logging.getLogger("scalevexo.ai")

PROVIDER_URL = "https://api.openai.com/v1/responses"
# Generous enough for a short completion, short enough that a hung provider
# does not hold a worker thread for minutes.
REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=45.0, write=10.0, pool=5.0)


def _require(membership: Membership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise NotAuthorized(f"This role may not perform {permission}.")


def _authorised_activities(membership: Membership, activity_ids: list[uuid.UUID]) -> list[Activity]:
    """Load only the activities this actor may actually read.

    Filtering here, rather than trusting the identifiers the client sent, is
    what stops a crafted request from summarising a colleague's — or another
    client's — notes.
    """
    workspace_id = current_workspace_id()
    queryset = Activity.objects.filter(
        id__in=activity_ids, workspace_id=workspace_id, deleted_at__isnull=True
    ).select_related("contact", "opportunity")

    if membership.role not in {Role.OWNER, Role.ADMIN, Role.SALES_MANAGER}:
        queryset = queryset.filter(author_id=membership.user_id)

    activities = list(queryset)
    if len(activities) != len(set(activity_ids)):
        raise ValidationFailed(
            "Some of the selected notes do not exist or are not yours to read.",
            code="unauthorised_sources",
        )
    if not activities:
        raise ValidationFailed("Select at least one note.")

    # Mixing clients in one prompt is how cross-client disclosure happens. RD08
    # tests this case explicitly.
    contact_ids = {a.contact_id for a in activities}
    if len(contact_ids) > 1:
        raise ValidationFailed(
            "Select notes for one contact at a time.",
            code="mixed_contacts",
        )
    return activities


def _render_sources(activities: list[Activity]) -> list[dict[str, Any]]:
    """Only the fields the task needs. Nothing commercial, nothing personal."""
    return [
        {
            "source_id": str(activity.id),
            "kind": activity.kind,
            "occurred_at": activity.occurred_at.isoformat(),
            # Labelled so the model does not present a claim as a fact.
            "evidence": activity.evidence_type,
            "outcome": activity.outcome or "(no outcome recorded)",
        }
        for activity in activities
    ]


def create_draft(
    *,
    membership: Membership,
    actor: User,
    purpose: str,
    activity_ids: list[uuid.UUID],
    workspace_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    request_key: str | None = None,
    request_id: str = "",
) -> AIDraft:
    """Produce a summary or a follow-up draft for human review.

    Deliberately **not** wrapped in one transaction. Each phase opens its own
    short ``workspace_context`` and commits, because an external provider call
    happens in the middle of this function.

    If the whole thing were atomic, a provider timeout would roll back the very
    budget reservation that is supposed to outlive it — the spend would be
    invisible until the invoice arrived. The same applies to a rejected output:
    RD08 needs those failures recorded, and a rollback would erase them.
    """
    _require(membership, "ai.use")

    if purpose not in DraftPurpose.values:
        raise ValidationFailed("Unknown AI purpose.")

    workspace_id = workspace_id or membership.workspace_id

    # --- Phase 1: authorise inputs and build the prompt (reads only) --------
    with workspace_context(workspace_id):
        activities = _authorised_activities(membership, activity_ids)
        sources = _render_sources(activities)
        opportunity = _resolve_opportunity(opportunity_id, workspace_id, activities)
        if opportunity is not None:
            sources.append(_render_opportunity(opportunity))
        contact = activities[0].contact
        input_versions = {str(a.id): a.version for a in activities}

    source_block = build_source_block(sources)
    prompt = f"{SYSTEM_PROMPT}\n\n{source_block}\n\n{_task_instruction(purpose)}"
    permitted_ids = {entry["source_id"] for entry in sources}

    # --- Phase 2: reserve budget, then record the draft. Both commit. ------
    key = request_key or uuid.uuid4().hex
    with workspace_context(workspace_id):
        reservation = budget.reserve(
            actor=actor, request_key=key, purpose=purpose, prompt_text=prompt
        )
        existing = AIDraft.objects.filter(reservation=reservation.record).first()
        if reservation.replayed and existing is not None:
            return existing

        draft = AIDraft.objects.create(
            workspace_id=workspace_id,
            purpose=purpose,
            reservation=reservation.record,
            requested_by=actor,
            contact=contact,
            opportunity=opportunity,
            source_ids=sorted(permitted_ids),
            input_versions=input_versions,
            prompt_version=PROMPT_VERSION,
            model_name=settings.AI_MODEL,
        )

    reservation_id = reservation.record.id
    max_output = reservation.record.max_output_tokens
    estimated_input = reservation.record.estimated_input_tokens

    # --- Phase 3: the provider call, outside any transaction ---------------
    try:
        raw, usage = _dispatch(prompt, max_output)
    except _AmbiguousProviderOutcome as exc:
        # The provider may have processed this and will bill for it either way.
        # Hold the reservation until somebody reconciles it (section 10.3).
        with workspace_context(workspace_id):
            budget.mark_uncertain(reservation_id=reservation_id, note=str(exc))
            _mark_draft_failed(draft, "The request did not complete. Try again later.")
        raise ValidationFailed(
            "The assistant did not respond. Your notes are unchanged; continue without it.",
            code="ai_unavailable",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - a known non-dispatch
        # A definite non-dispatch: nothing was charged, so release the hold.
        with workspace_context(workspace_id):
            budget.release(reservation_id=reservation_id, note=str(exc)[:300])
            _mark_draft_failed(draft, "The assistant is unavailable.")
        logger.warning("AI request failed before dispatch: %s", exc)
        raise ValidationFailed(
            "The assistant is unavailable. Your notes are unchanged; continue without it.",
            code="ai_unavailable",
        ) from exc

    # --- Phase 4: settle the real cost, then validate ----------------------
    with workspace_context(workspace_id):
        budget.settle(
            reservation_id=reservation_id,
            input_tokens=usage.get("input_tokens", estimated_input),
            output_tokens=usage.get("output_tokens", 0),
        )

    try:
        validated = validate_output(raw=raw, purpose=purpose, permitted_source_ids=permitted_ids)
    except OutputRejected as exc:
        # Rejected output is retained, not discarded: RD08 needs the failures.
        with workspace_context(workspace_id):
            _mark_draft_failed(draft, str(exc)[:300])
        logger.warning("AI output rejected: %s", exc)
        raise ValidationFailed(
            "The assistant returned something that could not be verified against "
            "your notes, so it has not been shown. Your notes are unchanged.",
            code="ai_output_rejected",
        ) from exc

    with workspace_context(workspace_id):
        draft.output = validated.model_dump()
        draft.uncertainties = validated.uncertainties
        draft.save(update_fields=["output", "uncertainties", "updated_at"])
        record_audit(
            action=AuditAction.CREATE,
            entity_type="ai.AIDraft",
            entity_id=draft.id,
            actor=actor,
            # The generated text is not audited; the draft row already holds it.
            changes={"purpose": purpose, "source_count": len(permitted_ids)},
            request_id=request_id,
        )
    return draft


def _mark_draft_failed(draft: AIDraft, message: str) -> None:
    draft.validation_error = message[:300]
    draft.save(update_fields=["validation_error", "updated_at"])


def _resolve_opportunity(
    opportunity_id: uuid.UUID | None, workspace_id: uuid.UUID, activities: list[Activity]
) -> Opportunity | None:
    if opportunity_id is None:
        return None
    opportunity = Opportunity.objects.filter(
        pk=opportunity_id, workspace_id=workspace_id, deleted_at__isnull=True
    ).first()
    if opportunity is None:
        raise ValidationFailed("That deal is not available.")
    if opportunity.contact_id != activities[0].contact_id:
        raise ValidationFailed("The notes and the deal are for different contacts.")
    return opportunity


def _render_opportunity(opportunity: Opportunity) -> dict[str, Any]:
    """Approved facts only.

    No amount, no margin, no internal commentary: the model must not be in a
    position to quote a price (section 10.2).
    """
    return {
        "source_id": str(opportunity.id),
        "kind": "opportunity",
        "service": opportunity.service,
        "stage": opportunity.stage,
        "expected_close_on": (
            opportunity.expected_close_on.isoformat()
            if opportunity.expected_close_on
            else "unknown"
        ),
    }


def _task_instruction(purpose: str) -> str:
    if purpose == DraftPurpose.SUMMARISE_NOTES:
        return (
            "Summarise the source notes for a colleague picking this up cold. "
            "Cite every claim with its source_id."
        )
    return (
        "Draft a short, plain follow-up email based only on the source notes. "
        "Do not state any price, discount, deadline or contractual commitment "
        "that is not written in the sources. Cite every claim with its "
        "source_id."
    )


class _AmbiguousProviderOutcome(RuntimeError):
    """The request may or may not have been processed."""


def _dispatch(prompt: str, max_output_tokens: int) -> tuple[dict[str, Any], dict]:
    """Call the provider.

    Direct HTTP through a single adapter rather than an SDK: two drafting
    functions do not justify a framework, and this way the retry behaviour is
    ours. SDK-level automatic retries are exactly what section 10.3 forbids,
    because a retry outside the reservation is unbudgeted spend.
    """
    api_key = settings.env("OPENAI_API_KEY") if hasattr(settings, "env") else None
    api_key = api_key or getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("No provider API key is configured.")

    payload = {
        "model": settings.AI_MODEL,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
        "temperature": 0.2,
        "text": {"format": {"type": "json_object"}},
    }

    try:
        # No retries. One attempt, one reservation.
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.post(
                PROVIDER_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        # We do not know whether the provider processed this.
        raise _AmbiguousProviderOutcome(f"{type(exc).__name__}: {exc}") from exc

    if response.status_code >= 500:
        raise _AmbiguousProviderOutcome(f"Provider returned {response.status_code}.")
    if response.status_code != 200:
        # A 4xx is a definite non-dispatch: nothing was charged.
        raise RuntimeError(f"Provider rejected the request: {response.status_code}")

    import json

    body = response.json()
    usage = {
        "input_tokens": body.get("usage", {}).get("input_tokens", 0),
        "output_tokens": body.get("usage", {}).get("output_tokens", 0),
    }
    text = _extract_text(body)
    try:
        return json.loads(text), usage
    except json.JSONDecodeError as exc:
        raise OutputRejected("Response was not valid JSON.") from exc


def _extract_text(body: dict[str, Any]) -> str:
    """Pull the text payload out of the provider response."""
    if "output_text" in body:
        return body["output_text"]
    for item in body.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                return content.get("text", "")
    raise OutputRejected("Response contained no text output.")


@transaction.atomic
def record_decision(
    *,
    membership: Membership,
    actor: User,
    draft_id: uuid.UUID,
    accepted: bool,
    reason: str = "",
) -> AIDraft:
    """Record that a person accepted or rejected a draft.

    Feeds the RD08 evaluation. Acceptance means "I found this useful"; it does
    not cause the draft to be sent or applied anywhere.
    """
    _require(membership, "ai.use")
    draft = AIDraft.objects.select_for_update().get(
        pk=draft_id, workspace_id=current_workspace_id()
    )
    if draft.requested_by_id != actor.id and membership.role not in {
        Role.OWNER,
        Role.ADMIN,
    }:
        raise NotAuthorized("This draft belongs to another user.")

    if accepted:
        draft.accepted_at = timezone.now()
        draft.rejected_at = None
    else:
        draft.rejected_at = timezone.now()
        draft.accepted_at = None
        draft.rejection_reason = reason
    draft.save()
    return draft
