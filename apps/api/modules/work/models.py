"""Tasks and follow-ups (CRM05).

A task requires an owner and a due time; completing it requires an outcome.
Rescheduling requires a reason and preserves the previous deadline -- a changed
deadline must not erase the original missed commitment (CRM05 acceptance).

Projects, milestones and tickets (CRM07-CRM09) are Stage 2 and are not modelled
here yet.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from modules.common.models import WorkspaceScopedModel


class TaskKind(models.TextChoices):
    FOLLOW_UP = "follow_up", "Follow-up"
    CALL = "call", "Call"
    EMAIL = "email", "Email"
    MEETING = "meeting", "Meeting"
    ADMIN = "admin", "Administrative"
    DELIVERY = "delivery", "Delivery work"


class TaskStatus(models.TextChoices):
    OPEN = "open", "Open"
    BLOCKED = "blocked", "Blocked"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class TaskOrigin(models.TextChoices):
    """Who created the task.

    Kept explicit so that the exception view can distinguish a commitment a
    person made from one a rule generated on their behalf.
    """

    USER = "user", "Created by a user"
    RULE = "rule", "Created by an automation rule"
    IMPORT = "import", "Created during import"
    CONVERSION = "conversion", "Created by deal conversion"


class Task(WorkspaceScopedModel):
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default="")
    kind = models.CharField(max_length=20, choices=TaskKind.choices, default=TaskKind.FOLLOW_UP)
    status = models.CharField(max_length=16, choices=TaskStatus.choices, default=TaskStatus.OPEN)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_tasks",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    origin = models.CharField(max_length=16, choices=TaskOrigin.choices, default=TaskOrigin.USER)

    # Server-calculated instant, plus the calendar version that produced it.
    # Recomputing later from an edited calendar would move a commitment
    # somebody has already been measured against (section 8.3).
    due_at = models.DateTimeField()
    calendar_version = models.PositiveIntegerField(default=1)
    # The first deadline ever set. Never overwritten by a reschedule, so the
    # original missed commitment stays visible (CRM05 acceptance).
    original_due_at = models.DateTimeField()
    reschedule_count = models.PositiveIntegerField(default=0)

    # Optional links to what the task is about.
    contact = models.ForeignKey(
        "crm.Contact", null=True, blank=True, on_delete=models.CASCADE, related_name="tasks"
    )
    opportunity = models.ForeignKey(
        "crm.Opportunity",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    lead = models.ForeignKey(
        "crm.Lead", null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )
    # Delivery work (CRM08: projects contain milestones and tasks). A task may
    # belong to a project, and to one of its milestones.
    project = models.ForeignKey(
        "work.Project", null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )
    milestone = models.ForeignKey(
        "work.Milestone", null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )

    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Completion requires an outcome, or an explicit reason for stopping
    # without a next action (CRM05).
    outcome = models.TextField(blank=True, default="")
    stop_reason = models.TextField(blank=True, default="")
    blocked_reason = models.TextField(blank=True, default="")
    cancelled_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "work_task"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(status=TaskStatus.COMPLETED)
                | (~models.Q(outcome="") | ~models.Q(stop_reason="")),
                name="ck_task_completion_requires_outcome",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=TaskStatus.BLOCKED) | ~models.Q(blocked_reason=""),
                name="ck_task_blocked_requires_reason",
            ),
        ]
        indexes = [
            # The Today queue: open work for one person, in due order.
            models.Index(
                fields=["workspace", "owner", "due_at"],
                condition=models.Q(status="open", deleted_at__isnull=True),
                name="idx_task_open_queue",
            ),
            models.Index(fields=["workspace", "due_at"]),
            models.Index(fields=["workspace", "opportunity", "status"]),
            models.Index(fields=["workspace", "owner", "status"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_open(self) -> bool:
        return self.status in {TaskStatus.OPEN, TaskStatus.BLOCKED}


class TaskReschedule(models.Model):
    """Append-only record of every deadline change (CRM05).

    Repeated rescheduling is a signal the exception view surfaces for review
    (rule A08). It is a prompt for a conversation, never an automatic
    disciplinary decision.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="task_reschedules"
    )
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="reschedules")
    previous_due_at = models.DateTimeField()
    new_due_at = models.DateTimeField()
    reason = models.TextField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    changed_at = models.DateTimeField()

    class Meta:
        db_table = "work_task_reschedule"
        indexes = [
            models.Index(fields=["workspace", "task", "changed_at"]),
            models.Index(fields=["workspace", "actor", "changed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.previous_due_at:%Y-%m-%d} -> {self.new_due_at:%Y-%m-%d}"


class ProjectStatus(models.TextChoices):
    PLANNED = "planned", "Planned"
    IN_PROGRESS = "in_progress", "In progress"
    BLOCKED = "blocked", "Blocked"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class Project(WorkspaceScopedModel):
    """Delivery work for one client (CRM08).

    Created by conversion from a template, then owned by delivery. The link back
    to the originating opportunity is what lets a delivery manager see what
    sales actually promised without asking for it.
    """

    client = models.ForeignKey("crm.Client", on_delete=models.CASCADE, related_name="projects")
    originating_opportunity = models.ForeignKey(
        "crm.Opportunity",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="projects",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.PLANNED
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_projects",
    )
    template_key = models.CharField(max_length=64, blank=True, default="")
    # Set once by conversion. Unique, so retrying a conversion after a timeout
    # returns the existing project instead of creating a second one (CRM07).
    conversion_key = models.CharField(max_length=128, blank=True, default="")
    starts_on = models.DateField(null=True, blank=True)
    due_on = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "work_project"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "conversion_key"],
                condition=models.Q(conversion_key__gt=""),
                name="uniq_project_conversion_key",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "client", "status"]),
            models.Index(fields=["workspace", "owner", "status"]),
        ]

    def __str__(self) -> str:
        return self.name


class MilestoneStatus(models.TextChoices):
    """SVX-PRD-001 section 5.1.

    IN_REVIEW and ACCEPTED are separate states with separate actors: an employee
    submits, an authorised reviewer accepts. Collapsing them would let somebody
    sign off their own work (CRM08).
    """

    PLANNED = "planned", "Planned"
    READY = "ready", "Ready"
    IN_PROGRESS = "in_progress", "In progress"
    BLOCKED = "blocked", "Blocked"
    IN_REVIEW = "in_review", "Submitted for review"
    ACCEPTED = "accepted", "Accepted"
    CANCELLED = "cancelled", "Cancelled"


class Milestone(WorkspaceScopedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="milestones")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    sequence = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=MilestoneStatus.choices, default=MilestoneStatus.PLANNED
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_milestones",
    )
    due_on = models.DateField(null=True, blank=True)

    # Completion submitted by an employee is distinct from acceptance by an
    # authorised reviewer (CRM08).
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    evidence = models.TextField(blank=True, default="")
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accepted_milestones",
    )
    acceptance_note = models.TextField(blank=True, default="")

    # Blocked work requires a blocker description and a next owner (CRM08).
    blocked_reason = models.TextField(blank=True, default="")
    blocked_next_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    cancelled_reason = models.TextField(blank=True, default="")
    cancellation_impact = models.TextField(blank=True, default="")

    # An acceptance a manager overrode despite an incomplete predecessor.
    # Recorded rather than silently permitted, so the exception is reviewable.
    dependency_override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    dependency_override_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "work_milestone"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(status=MilestoneStatus.IN_REVIEW) | ~models.Q(evidence=""),
                name="ck_milestone_review_requires_evidence",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=MilestoneStatus.BLOCKED) | ~models.Q(blocked_reason=""),
                name="ck_milestone_blocked_requires_reason",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=MilestoneStatus.CANCELLED)
                | ~models.Q(cancelled_reason=""),
                name="ck_milestone_cancelled_requires_reason",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "project", "sequence"]),
            models.Index(fields=["workspace", "status", "due_on"]),
            models.Index(fields=["workspace", "owner", "status"]),
        ]

    def __str__(self) -> str:
        return self.name


class MilestoneDependency(models.Model):
    """A milestone that cannot be accepted until its predecessor is accepted.

    Cycles are rejected at the service boundary by walking the graph before
    inserting. A cycle would make every milestone in it permanently
    unacceptable -- a deadlock a user can neither diagnose nor escape.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace",
        on_delete=models.CASCADE,
        related_name="milestone_dependencies",
    )
    milestone = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name="dependencies")
    depends_on = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name="dependents")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "work_milestone_dependency"
        constraints = [
            models.UniqueConstraint(
                fields=["milestone", "depends_on"], name="uniq_milestone_dependency"
            ),
            models.CheckConstraint(
                condition=~models.Q(milestone=models.F("depends_on")),
                name="ck_milestone_no_self_dependency",
            ),
        ]
        indexes = [models.Index(fields=["workspace", "depends_on"])]

    def __str__(self) -> str:
        return f"{self.milestone_id} depends on {self.depends_on_id}"


class ScopeChange(models.Model):
    """Scope changes recorded separately from defects (CRM08).

    Conflating them destroys the only evidence of why a project ran over: a
    defect is work we already owed, a scope change is work we agreed to add.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="scope_changes"
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="scope_changes")
    milestone = models.ForeignKey(
        Milestone, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    kind = models.CharField(
        max_length=16,
        choices=[("scope_change", "Scope change"), ("defect", "Defect")],
    )
    description = models.TextField()
    commercial_impact = models.TextField(blank=True, default="")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    recorded_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "work_scope_change"
        indexes = [models.Index(fields=["workspace", "project", "kind"])]

    def __str__(self) -> str:
        return f"{self.kind}: {self.description[:60]}"
