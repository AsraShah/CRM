"""Identity state changes (CRM01).

Every function here enforces its own permissions and invariants, because a
service may also be reached from a worker, a management command or a test --
not only from an authorised view.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from modules.common.audit import AuditAction, record_audit
from modules.common.calendars import WorkingCalendar, WorkingInterval
from modules.common.exceptions import NotAuthorized, ValidationFailed, VersionConflict
from modules.identity.models import (
    Invitation,
    Membership,
    MembershipStatus,
    Role,
    Team,
    User,
    Workspace,
)
from modules.identity.policy import has_permission

INVITATION_TTL_DAYS = 14


def _require(membership: Membership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise NotAuthorized(f"This role may not perform {permission}.")


def build_calendar(workspace: Workspace) -> WorkingCalendar:
    """Materialise the workspace's stored calendar configuration."""
    intervals = tuple(
        WorkingInterval(start=_parse_time(entry["start"]), end=_parse_time(entry["end"]))
        for entry in (workspace.working_intervals or [])
    )
    weekdays = frozenset(workspace.working_weekdays or [0, 1, 2, 3, 4])
    holidays = frozenset(
        datetime.strptime(day, "%Y-%m-%d").date()  # noqa: DTZ007 - a date, not an instant
        for day in (workspace.holidays or [])
    )
    calendar_kwargs = {
        "time_zone": workspace.time_zone,
        "version": workspace.calendar_version,
        "working_weekdays": weekdays,
        "holidays": holidays,
    }
    if intervals:
        calendar_kwargs["intervals"] = intervals
    return WorkingCalendar(**calendar_kwargs)


def _parse_time(value: str) -> time:
    """Parse a stored "HH:MM" working-interval boundary."""
    hour, _, minute = value.partition(":")
    return time(hour=int(hour), minute=int(minute or 0))


@transaction.atomic
def invite_member(
    *,
    actor_membership: Membership,
    actor: User,
    email: str,
    role: str,
    team: Team | None = None,
    request_id: str = "",
) -> tuple[Invitation, str]:
    """Create an invitation and return it with its single-use plaintext token.

    The token is returned once, to the caller, and only its hash is stored. A
    later database read cannot reconstruct a usable invitation.
    """
    _require(actor_membership, "invitation.manage")

    if role not in Role.values:
        raise ValidationFailed("Unknown role.", field_errors={"role": ["Unknown role."]})
    if role == Role.CLIENT:
        raise ValidationFailed(
            "Client accounts are a Release 3 capability and cannot be invited yet.",
            field_errors={"role": ["Not available in this release."]},
        )

    workspace = actor_membership.workspace
    normalised = User.objects.normalize_email(email).lower()

    existing = Membership.objects.filter(
        workspace=workspace, user__email__iexact=normalised
    ).first()
    if existing is not None:
        raise ValidationFailed(
            "That person is already a member of this workspace.",
            field_errors={"email": ["Already a member."]},
        )

    token = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        workspace=workspace,
        email=normalised,
        role=role,
        team=team,
        invited_by=actor,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(days=INVITATION_TTL_DAYS),
    )
    record_audit(
        action=AuditAction.CREATE,
        entity_type="identity.Invitation",
        entity_id=invitation.id,
        actor=actor,
        changes={"email": normalised, "role": role},
        request_id=request_id,
    )
    return invitation, token


@transaction.atomic
def suspend_member(
    *,
    actor_membership: Membership,
    actor: User,
    membership_id,
    reason: str,
    request_id: str = "",
) -> Membership:
    """Revoke access without deleting business history (CRM01).

    Sessions are invalidated here rather than left to expire, and the person's
    authored activities, audit events and assignments are all retained.
    """
    _require(actor_membership, "membership.manage")
    if not reason.strip():
        raise ValidationFailed(
            "A suspension must record a reason.",
            field_errors={"reason": ["This field is required."]},
        )

    membership = Membership.objects.select_for_update().get(
        pk=membership_id, workspace=actor_membership.workspace
    )
    if membership.pk == actor_membership.pk:
        raise ValidationFailed("You cannot suspend your own membership.")
    if membership.role == Role.OWNER:
        remaining_owners = (
            Membership.objects.filter(
                workspace=membership.workspace,
                role=Role.OWNER,
                status=MembershipStatus.ACTIVE,
            )
            .exclude(pk=membership.pk)
            .count()
        )
        if remaining_owners == 0:
            raise ValidationFailed("A workspace must retain at least one active owner.")

    membership.status = MembershipStatus.SUSPENDED
    membership.suspended_at = timezone.now()
    membership.suspended_reason = reason
    membership.is_available_for_assignment = False
    membership.save(
        update_fields=[
            "status",
            "suspended_at",
            "suspended_reason",
            "is_available_for_assignment",
            "updated_at",
        ]
    )

    _invalidate_sessions_for(membership.user)

    record_audit(
        action=AuditAction.UPDATE,
        entity_type="identity.Membership",
        entity_id=membership.id,
        actor=actor,
        changes={"status": {"from": MembershipStatus.ACTIVE, "to": membership.status}},
        reason=reason,
        request_id=request_id,
    )
    return membership


@transaction.atomic
def reinstate_member(
    *, actor_membership: Membership, actor: User, membership_id, request_id: str = ""
) -> Membership:
    _require(actor_membership, "membership.manage")
    membership = Membership.objects.select_for_update().get(
        pk=membership_id, workspace=actor_membership.workspace
    )
    membership.status = MembershipStatus.ACTIVE
    membership.suspended_at = None
    membership.suspended_reason = ""
    membership.is_available_for_assignment = True
    membership.save(
        update_fields=[
            "status",
            "suspended_at",
            "suspended_reason",
            "is_available_for_assignment",
            "updated_at",
        ]
    )
    record_audit(
        action=AuditAction.UPDATE,
        entity_type="identity.Membership",
        entity_id=membership.id,
        actor=actor,
        changes={"status": {"to": MembershipStatus.ACTIVE}},
        request_id=request_id,
    )
    return membership


def _invalidate_sessions_for(user: User) -> None:
    """Drop this user's active sessions.

    Scanning the session table is acceptable at pilot scale (10 users). If the
    session count grows, replace this with a per-user session index rather than
    letting the scan get slower silently.
    """
    from django.contrib.sessions.models import Session

    now = timezone.now()
    for session in Session.objects.filter(expire_date__gte=now).iterator():
        data = session.get_decoded()
        if str(data.get("_auth_user_id", "")) == str(user.pk):
            session.delete()


def assert_expected_version(instance, expected_version: int | None) -> None:
    """Optimistic concurrency guard shared by the mutating services.

    A missing expected version is rejected rather than treated as "latest": a
    client that forgets to send one would otherwise silently overwrite a
    colleague's change, which is exactly the outcome CRM03 forbids.
    """
    if expected_version is None:
        raise ValidationFailed(
            "An expected_version is required for this update.",
            field_errors={"expected_version": ["This field is required."]},
        )
    if instance.version != expected_version:
        raise VersionConflict(expected=expected_version, current=instance.version)


# ---------------------------------------------------------------------------
# Accepting an invitation
# ---------------------------------------------------------------------------

#: One message for every unusable token. Saying "expired" versus "unknown"
#: would tell a guesser which tokens once existed.
INVALID_INVITATION = (
    "This invitation is not valid. It may have expired, been used or been withdrawn."
)


def _pending_invitation(token: str, *, lock: bool = False) -> Invitation:
    """Find the pending invitation for a plaintext token, or refuse."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    queryset = Invitation.objects.select_related("workspace", "team")
    if lock:
        # Lock only the invitation row: the optional team is an outer join,
        # which PostgreSQL will not lock.
        queryset = queryset.select_for_update(of=("self",))
    invitation = queryset.filter(token_hash=token_hash).first()
    if invitation is None or not invitation.is_pending:
        raise ValidationFailed(INVALID_INVITATION, field_errors={"token": [INVALID_INVITATION]})
    return invitation


def describe_invitation(token: str) -> dict:
    """What the sign-up page shows before anyone commits to anything.

    `has_account` decides the flow: a new person chooses a password, while an
    existing account must sign in first, so the token can never be used to set
    somebody else's password.
    """
    invitation = _pending_invitation(token)
    return {
        "workspace_name": invitation.workspace.name,
        "email": invitation.email,
        "role": invitation.role,
        "expires_at": invitation.expires_at,
        "has_account": User.objects.filter(email__iexact=invitation.email).exists(),
    }


@transaction.atomic
def accept_invitation(
    *,
    token: str,
    signed_in_user: User | None,
    full_name: str = "",
    password: str = "",
    request_id: str = "",
) -> Membership:
    """Turn a pending invitation into an active membership (CRM01).

    - A new person gets an account with the invited email and the password they
      choose, validated by the configured password validators.
    - An existing account is never modified here. Its owner must be signed in
      as that account; otherwise holding the token would let anyone take it over.

    The email is marked verified: the token was issued to that address by an
    administrator and only reaches its recipient through them. The invitation is
    single-use and locked while it is consumed, so two concurrent acceptances
    cannot create two memberships.
    """
    from allauth.account.models import EmailAddress
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError as DjangoValidationError

    from modules.common.tenancy import workspace_context

    invitation = _pending_invitation(token, lock=True)
    existing = User.objects.filter(email__iexact=invitation.email).first()

    if existing is not None:
        if signed_in_user is None or signed_in_user.pk != existing.pk:
            raise NotAuthorized(
                "An account already exists for this email. Sign in as "
                f"{invitation.email}, then open the invitation again."
            )
        user = existing
    else:
        if signed_in_user is not None:
            raise ValidationFailed(
                f"This invitation is for {invitation.email}. Sign out first, then "
                "open it again to create that account."
            )
        candidate = User(email=invitation.email, full_name=full_name.strip())
        try:
            validate_password(password, user=candidate)
        except DjangoValidationError as exc:
            raise ValidationFailed(
                "Choose a stronger password.", field_errors={"password": list(exc.messages)}
            ) from exc
        user = User.objects.create_user(
            email=invitation.email, password=password, full_name=full_name.strip()
        )

    EmailAddress.objects.update_or_create(
        user=user,
        email=invitation.email,
        defaults={"verified": True, "primary": True},
    )

    if Membership.objects.filter(workspace=invitation.workspace, user=user).exists():
        raise ValidationFailed("You are already a member of this workspace.")

    membership = Membership.objects.create(
        workspace=invitation.workspace,
        user=user,
        role=invitation.role,
        team=invitation.team,
        status=MembershipStatus.ACTIVE,
        mfa_enrolled=False,
    )
    # Someone who already had a second factor keeps it in this workspace too.
    from modules.identity.signals import sync_mfa_enrolment

    sync_mfa_enrolment(user)

    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["accepted_at", "updated_at"])

    with workspace_context(invitation.workspace_id):
        record_audit(
            action=AuditAction.CREATE,
            entity_type="identity.Membership",
            entity_id=membership.id,
            actor=user,
            changes={"invitation_id": str(invitation.id), "role": invitation.role},
            request_id=request_id,
        )
    membership.refresh_from_db()
    return membership


# ---------------------------------------------------------------------------
# Workspace calendar (CRM05, CRM06)
# ---------------------------------------------------------------------------


@transaction.atomic
def configure_calendar(
    *,
    actor_membership: Membership,
    actor: User,
    time_zone: str,
    working_weekdays: list[int],
    working_intervals: list[dict],
    holidays: list[str],
    default_currency: str,
    request_id: str = "",
) -> Workspace:
    """Set the working calendar every deadline and rule delay is counted in.

    The calendar version is bumped on any change. Deadlines already computed
    keep the version that produced them, so editing the calendar cannot move a
    commitment somebody has already been measured against (section 8.3).
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    _require(actor_membership, "workspace.configure")
    errors: dict[str, list[str]] = {}

    try:
        ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        errors["time_zone"] = ["Use an IANA time zone, for example Asia/Karachi."]

    weekdays = sorted(set(working_weekdays))
    if not weekdays or any(day not in range(7) for day in weekdays):
        errors["working_weekdays"] = ["Choose at least one working day (0 is Monday)."]

    intervals = []
    for entry in working_intervals:
        try:
            start, end = _parse_time(entry["start"]), _parse_time(entry["end"])
        except (KeyError, ValueError, TypeError):
            errors["working_intervals"] = ["Each interval needs a start and end as HH:MM."]
            break
        if end <= start:
            errors["working_intervals"] = ["Each interval must end after it starts."]
            break
        intervals.append((start, end))
    intervals.sort()
    if any(intervals[i][1] > intervals[i + 1][0] for i in range(len(intervals) - 1)):
        errors["working_intervals"] = ["Working intervals must not overlap."]
    if not working_intervals:
        errors["working_intervals"] = ["Give at least one working interval."]

    cleaned_holidays = []
    for day in holidays:
        try:
            cleaned_holidays.append(datetime.strptime(day, "%Y-%m-%d").date().isoformat())  # noqa: DTZ007
        except (ValueError, TypeError):
            errors["holidays"] = [f"{day!r} is not a date (YYYY-MM-DD)."]
            break

    if len(default_currency) != 3 or not default_currency.isalpha():
        errors["default_currency"] = ["Use a three-letter currency code."]
    if errors:
        raise ValidationFailed("The calendar settings are not valid.", field_errors=errors)

    workspace = Workspace.objects.select_for_update().get(pk=actor_membership.workspace_id)
    before = {
        "time_zone": workspace.time_zone,
        "working_weekdays": workspace.working_weekdays,
        "working_intervals": workspace.working_intervals,
        "holidays": workspace.holidays,
        "default_currency": workspace.default_currency,
    }
    after = {
        "time_zone": time_zone,
        "working_weekdays": weekdays,
        "working_intervals": [
            {"start": s.strftime("%H:%M"), "end": e.strftime("%H:%M")} for s, e in intervals
        ],
        "holidays": sorted(set(cleaned_holidays)),
        "default_currency": default_currency.upper(),
    }
    calendar_changed = any(before[k] != after[k] for k in after if k != "default_currency")
    for key, value in after.items():
        setattr(workspace, key, value)
    if calendar_changed:
        workspace.calendar_version += 1
    workspace.save()

    record_audit(
        action=AuditAction.UPDATE,
        entity_type="identity.Workspace",
        entity_id=workspace.id,
        actor=actor,
        changes={
            key: {"from": before[key], "to": after[key]}
            for key in after
            if before[key] != after[key]
        },
        request_id=request_id,
    )
    return workspace
