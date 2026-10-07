"""Workspace, membership and identity (CRM01, SVX-TECH-001 sections 4.1, 5.2)."""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from modules.common.models import TimestampedModel


class Role(models.TextChoices):
    """Roles from SVX-PRD-001 section 1.2.

    Workspace administration is deliberately separate from ownership: an
    administrator manages accounts and configuration but gains no automatic
    authority over confidential employee information (section 5.2).
    """

    OWNER = "owner", "CEO and workspace owner"
    SALES_MANAGER = "sales_manager", "Sales manager"
    SALES_REP = "sales_rep", "Sales representative"
    DELIVERY_MANAGER = "delivery_manager", "Delivery manager"
    DELIVERY_EMPLOYEE = "delivery_employee", "Delivery employee"
    ADMIN = "admin", "Workspace administrator"
    CLIENT = "client", "Client user (Release 3)"


class MembershipStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    # Suspension revokes access without deleting business history (CRM01).
    SUSPENDED = "suspended", "Suspended"
    INVITED = "invited", "Invited, not yet accepted"


class UserManager(BaseUserManager):
    """Email-identified users. There is no public signup in Release 1."""

    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("A superuser must have is_staff and is_superuser set.")
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """A person who can sign in.

    A user is global; their authority is always expressed through a Membership
    in a specific workspace. Deactivating the user here is an account-level
    block; suspending a membership is the per-workspace control.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=200, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "identity_user"

    def __str__(self) -> str:
        return self.email


class Workspace(TimestampedModel):
    """One tenant. Release 1 runs a single workspace; the boundary exists from
    the start so that Release 3 does not require retrofitting isolation
    (ADR007)."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    time_zone = models.CharField(max_length=64, default="Asia/Karachi")
    # Bumped whenever working hours or holidays change. Deadlines persist the
    # version that produced them (section 8.3).
    calendar_version = models.PositiveIntegerField(default=1)
    working_weekdays = models.JSONField(default=list, blank=True)
    working_intervals = models.JSONField(default=list, blank=True)
    holidays = models.JSONField(default=list, blank=True)
    default_currency = models.CharField(max_length=3, default="USD")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "identity_workspace"

    def __str__(self) -> str:
        return self.name


class Team(TimestampedModel):
    """A grouping used for manager visibility (sales team, delivery team)."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=120)

    class Meta:
        db_table = "identity_team"
        constraints = [models.UniqueConstraint(fields=["workspace", "name"], name="uniq_team_name")]

    def __str__(self) -> str:
        return self.name


class Membership(TimestampedModel):
    """A user's role and status inside one workspace."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
    )
    role = models.CharField(max_length=32, choices=Role.choices)
    status = models.CharField(
        max_length=16, choices=MembershipStatus.choices, default=MembershipStatus.INVITED
    )
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_reason = models.TextField(blank=True, default="")
    # Set false while a required second factor is not yet enrolled. Privileged
    # roles cannot act until it is (CRM01).
    mfa_enrolled = models.BooleanField(default=False)
    # Absent members must not receive new automatic assignments (rule catalogue
    # preamble). Set by leave routing in Release 2; honoured by the rule engine
    # from Release 1.
    is_available_for_assignment = models.BooleanField(default=True)

    class Meta:
        db_table = "identity_membership"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"], name="uniq_membership_per_workspace"
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "status"]),
            models.Index(fields=["workspace", "role"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} as {self.role}"

    @property
    def is_active(self) -> bool:
        return self.status == MembershipStatus.ACTIVE and self.user.is_active

    @property
    def can_receive_assignment(self) -> bool:
        return self.is_active and self.is_available_for_assignment


class Invitation(TimestampedModel):
    """An outstanding invitation. Access is invitation-only (CRM01)."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=32, choices=Role.choices)
    team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    invited_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL, related_name="sent_invitations"
    )
    # Stored as a hash: a leaked database row must not yield a usable invite.
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_invitation"
        indexes = [models.Index(fields=["workspace", "email"])]

    @property
    def is_pending(self) -> bool:
        return (
            self.accepted_at is None
            and self.revoked_at is None
            and self.expires_at > timezone.now()
        )
