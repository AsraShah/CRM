"""AI spend control (SVX-TECH-001 section 10.3).

The application is the hard stop. A provider dashboard alert is secondary — it
tells you about money you have already spent.

Reserve before dispatch, settle afterwards:

    estimate max cost -> lock the period row -> reserve or deny
                      -> dispatch -> settle actual usage

The lock is what makes concurrency safe. Without it, two requests read the same
remaining budget, both conclude there is room, and both spend it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.ai.models import (
    AIUsageReservation,
    BudgetPeriod,
    ReservationState,
)
from modules.common.exceptions import BudgetExceeded, ValidationFailed
from modules.common.tenancy import current_workspace_id

MILLION = Decimal(1_000_000)

# A conservative characters-per-token ratio. Deliberately pessimistic: an
# underestimate lets a request through that the budget cannot cover, which is
# the failure that matters. RD08 validates this estimator against real usage
# before AI is enabled.
CHARS_PER_TOKEN = Decimal("3.0")


@dataclass(slots=True)
class Reservation:
    record: AIUsageReservation
    period: BudgetPeriod
    #: True when an identical request key already held a reservation.
    replayed: bool


def estimate_input_tokens(text: str) -> int:
    """Upper bound on the input tokens a string will use.

    Not a tokenizer. A bounded, conservative estimate is what section 10.3
    permits, and an estimate that errs high is the safe direction: it can only
    cause a request to be denied, never to overspend.
    """
    return int((Decimal(len(text)) / CHARS_PER_TOKEN).to_integral_value(ROUND_CEILING))


def cost_usd(input_tokens: int, output_tokens: int) -> Decimal:
    """Published-rate arithmetic. Not a measured cost."""
    input_rate = Decimal(str(settings.AI_INPUT_USD_PER_MTOK))
    output_rate = Decimal(str(settings.AI_OUTPUT_USD_PER_MTOK))
    total = (Decimal(input_tokens) / MILLION * input_rate) + (
        Decimal(output_tokens) / MILLION * output_rate
    )
    # Round up to the stored precision. Rounding down would let a long tail of
    # sub-cent requests escape the ceiling entirely.
    return total.quantize(Decimal("0.0001"), rounding=ROUND_CEILING)


def current_period(*, create: bool = True) -> BudgetPeriod | None:
    """This calendar month's budget row for the active workspace."""
    workspace_id = current_workspace_id()
    period_start = timezone.now().date().replace(day=1)

    period = BudgetPeriod.objects.filter(
        workspace_id=workspace_id, period_start=period_start
    ).first()
    if period is not None or not create:
        return period

    try:
        with transaction.atomic():
            return BudgetPeriod.objects.create(
                workspace_id=workspace_id,
                period_start=period_start,
                ceiling_usd=Decimal(str(settings.AI_MONTHLY_CEILING_USD)),
            )
    except IntegrityError:
        # Another request created it first.
        return BudgetPeriod.objects.get(workspace_id=workspace_id, period_start=period_start)


@transaction.atomic
def reserve(
    *,
    actor,
    request_key: str,
    purpose: str,
    prompt_text: str,
    max_output_tokens: int | None = None,
) -> Reservation:
    """Claim budget for one request, or refuse it.

    Raises ``BudgetExceeded`` when the reservation would exceed the remaining
    monthly ceiling, when the kill switch is on, or when a concurrent request is
    already in flight.
    """
    if not settings.FEATURE_AI_ENABLED:
        raise BudgetExceeded(
            "AI assistance is disabled for this workspace.",
            code="ai_disabled",
        )

    workspace_id = current_workspace_id()
    output_tokens = max_output_tokens or settings.AI_MAX_OUTPUT_TOKENS
    if output_tokens > settings.AI_MAX_OUTPUT_TOKENS:
        raise ValidationFailed(f"Output is limited to {settings.AI_MAX_OUTPUT_TOKENS} tokens.")

    input_tokens = estimate_input_tokens(prompt_text)
    if input_tokens > settings.AI_MAX_INPUT_TOKENS:
        raise ValidationFailed(
            f"This request is too large: about {input_tokens} input tokens "
            f"against a limit of {settings.AI_MAX_INPUT_TOKENS}. Select fewer "
            "notes.",
            code="input_too_large",
        )

    # An existing reservation under the same key is a retry, not a new request.
    existing = AIUsageReservation.objects.filter(
        workspace_id=workspace_id, request_key=request_key
    ).first()
    if existing is not None:
        return Reservation(
            record=existing,
            period=existing.period,
            replayed=True,
        )

    # Lock the period row. Every budget decision for this workspace serialises
    # here, which is exactly what one-concurrent-request means in practice.
    period = current_period()
    period = BudgetPeriod.objects.select_for_update().get(pk=period.pk)

    if period.disabled_at is not None:
        raise BudgetExceeded(
            "AI assistance has been switched off by an administrator.",
            code="ai_kill_switch",
            extra={"reason": period.disabled_reason},
        )

    in_flight = AIUsageReservation.objects.filter(
        workspace_id=workspace_id, state=ReservationState.RESERVED
    ).count()
    if in_flight >= settings.AI_MAX_CONCURRENT_REQUESTS:
        raise BudgetExceeded(
            "Another AI request is already running. Wait for it to finish.",
            code="ai_concurrency_limit",
        )

    # Reserve the maximum this request could cost, not the expected cost.
    reserved = cost_usd(input_tokens, output_tokens)
    if reserved > period.remaining_usd:
        raise BudgetExceeded(
            "This request would exceed the monthly AI budget. Core functions are unaffected.",
            code="ai_budget_exhausted",
            extra={
                "ceiling_usd": str(period.ceiling_usd),
                "committed_usd": str(period.committed_usd),
                "requested_usd": str(reserved),
            },
        )

    if _daily_requests_for(actor) >= settings.AI_DAILY_REQUESTS_PER_USER:
        raise BudgetExceeded(
            "You have reached your daily AI request allowance.",
            code="ai_daily_limit",
        )

    record = AIUsageReservation.objects.create(
        workspace_id=workspace_id,
        period=period,
        actor=actor,
        request_key=request_key,
        purpose=purpose,
        reserved_usd=reserved,
        estimated_input_tokens=input_tokens,
        max_output_tokens=output_tokens,
        state=ReservationState.RESERVED,
    )

    period.reserved_usd += reserved
    period.save(update_fields=["reserved_usd"])

    return Reservation(record=record, period=period, replayed=False)


def _daily_requests_for(actor) -> int:
    start_of_day = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return AIUsageReservation.objects.filter(
        workspace_id=current_workspace_id(),
        actor=actor,
        created_at__gte=start_of_day,
    ).count()


@transaction.atomic
def settle(
    *,
    reservation_id: uuid.UUID,
    input_tokens: int,
    output_tokens: int,
) -> AIUsageReservation:
    """Record actual usage and release the unused reservation."""
    record = AIUsageReservation.objects.select_for_update().get(pk=reservation_id)
    if record.state != ReservationState.RESERVED:
        return record

    period = BudgetPeriod.objects.select_for_update().get(pk=record.period_id)
    actual = cost_usd(input_tokens, output_tokens)

    record.actual_input_tokens = input_tokens
    record.actual_output_tokens = output_tokens
    record.actual_usd = actual
    record.state = ReservationState.SETTLED
    record.settled_at = timezone.now()
    record.save()

    period.reserved_usd -= record.reserved_usd
    period.settled_usd += actual
    period.save(update_fields=["reserved_usd", "settled_usd"])
    return record


@transaction.atomic
def release(*, reservation_id: uuid.UUID, note: str = "") -> AIUsageReservation:
    """Release a reservation for a request that definitely did not run.

    Only for a *known* non-dispatch: a validation failure before the call, or a
    refusal. An ambiguous outcome uses ``mark_uncertain`` instead.
    """
    record = AIUsageReservation.objects.select_for_update().get(pk=reservation_id)
    if record.state != ReservationState.RESERVED:
        return record

    period = BudgetPeriod.objects.select_for_update().get(pk=record.period_id)
    record.state = ReservationState.RELEASED
    record.settled_at = timezone.now()
    record.failure_note = note[:300]
    record.save()

    period.reserved_usd -= record.reserved_usd
    period.save(update_fields=["reserved_usd"])
    return record


@transaction.atomic
def mark_uncertain(*, reservation_id: uuid.UUID, note: str) -> AIUsageReservation:
    """A timed-out or unreadable request keeps its reservation.

    The provider may well have processed it and will certainly bill for it.
    Releasing the reservation here would let the same budget be spent twice and
    would hide the discrepancy until the invoice arrived. It stays held until
    somebody reconciles it against the provider (section 10.3).
    """
    record = AIUsageReservation.objects.select_for_update().get(pk=reservation_id)
    record.state = ReservationState.UNCERTAIN
    record.failure_note = note[:300]
    record.save(update_fields=["state", "failure_note"])
    return record


@transaction.atomic
def set_kill_switch(*, actor, enabled: bool, reason: str = "") -> BudgetPeriod:
    """Administrator switch, independent of the ceiling (section 10.3)."""
    period = BudgetPeriod.objects.select_for_update().get(pk=current_period().pk)
    if enabled:
        period.disabled_at = None
        period.disabled_by = None
        period.disabled_reason = ""
    else:
        period.disabled_at = timezone.now()
        period.disabled_by = actor
        period.disabled_reason = reason
    period.save(update_fields=["disabled_at", "disabled_by", "disabled_reason"])
    return period


def status() -> dict:
    """Current budget position, for the administration screen."""
    period = current_period(create=False)
    if period is None:
        return {
            "enabled": settings.FEATURE_AI_ENABLED,
            "ceiling_usd": str(settings.AI_MONTHLY_CEILING_USD),
            "committed_usd": "0.0000",
            "remaining_usd": str(settings.AI_MONTHLY_CEILING_USD),
            "uncertain_reservations": 0,
        }
    return {
        "enabled": settings.FEATURE_AI_ENABLED and period.disabled_at is None,
        "period_start": period.period_start.isoformat(),
        "ceiling_usd": str(period.ceiling_usd),
        "reserved_usd": str(period.reserved_usd),
        "settled_usd": str(period.settled_usd),
        "committed_usd": str(period.committed_usd),
        "remaining_usd": str(period.remaining_usd),
        # Surfaced because held-but-unreconciled reservations silently shrink
        # the available budget until somebody looks at them.
        "uncertain_reservations": AIUsageReservation.objects.filter(
            period=period, state=ReservationState.UNCERTAIN
        ).count(),
        "note": (
            "Costs are arithmetic at published rates, not measured spend. "
            "Reconcile against the provider invoice."
        ),
    }
