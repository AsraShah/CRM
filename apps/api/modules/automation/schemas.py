"""Rule configuration schema (SVX-TECH-001 section 8.1).

A small, versioned JSON shape validated by Pydantic. Only approved fields,
operators and actions are accepted.

What is deliberately *not* accepted from a rule author: Python, JavaScript, SQL,
arbitrary URLs and unbounded regular expressions. A rule is configuration, not
code, and the set of things it can express is closed.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Fields a condition may read. Anything outside this list is rejected, so a
# rule cannot be pointed at commercial or personal data it was never meant to
# see.
ALLOWED_CONDITION_FIELDS = frozenset(
    {
        "lead.status",
        "lead.owner_id",
        "lead.source",
        "opportunity.stage",
        "opportunity.owner_id",
        "opportunity.next_action_at",
        "opportunity.last_activity_at",
        "task.status",
        "task.kind",
        "task.owner_id",
        "task.due_at",
        "task.reschedule_count",
        # Delivery and support. Note what is absent: no amount, no currency, no
        # commercial reference. A rule has no business branching on deal value.
        "client.status",
        "client.delivery_owner_id",
        "milestone.status",
        "milestone.owner_id",
        "milestone.due_on",
        "ticket.state",
        "ticket.priority",
        "ticket.owner_id",
    }
)

ALLOWED_OPERATORS = ("eq", "neq", "in", "is_empty", "gt", "lt")

# Release 1 actions. Note what is absent: no rule can mark a deal won, accept a
# milestone, alter a receipt or suspend an employee (section 8.1).
ALLOWED_ACTIONS = ("create_task", "assign_owner", "notify_owner", "notify_manager")

ALLOWED_TRIGGERS = (
    "lead.created",
    "lead.assigned",
    "opportunity.created",
    "opportunity.stage_changed",
    "opportunity.next_action_cleared",
    "task.due",
    "task.completed",
    "task.rescheduled",
    "activity.recorded",
    # Delivery and support (CRM07-CRM09).
    "client.handover_requested",
    "client.handover_accepted",
    "client.handover_returned",
    "milestone.submitted",
    "milestone.accepted",
    "milestone.blocked",
    "ticket.created",
    "ticket.state_changed",
)


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    op: Literal["eq", "neq", "in", "is_empty", "gt", "lt"]
    value: Any = None

    @field_validator("field")
    @classmethod
    def _known_field(cls, value: str) -> str:
        if value not in ALLOWED_CONDITION_FIELDS:
            raise ValueError(
                f"Unknown condition field {value!r}. Allowed: {sorted(ALLOWED_CONDITION_FIELDS)}"
            )
        return value

    @model_validator(mode="after")
    def _value_matches_operator(self) -> Condition:
        if self.op == "is_empty":
            if self.value not in (None, True, False):
                raise ValueError("is_empty takes no value, or a boolean.")
        elif self.op == "in":
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("The 'in' operator requires a non-empty list.")
            if len(self.value) > 50:
                raise ValueError("An 'in' list may hold at most 50 entries.")
        elif self.value is None:
            raise ValueError(f"Operator {self.op!r} requires a value.")
        return self


class Delay(BaseModel):
    """A delay expressed in working time, never wall-clock time.

    "2 working hours" on a Friday afternoon must land on Monday morning. The
    conversion is done by modules.common.calendars against the workspace
    calendar (RD07).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    working_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 30)
    working_hours: int | None = Field(default=None, ge=0, le=24 * 30)
    working_days: int | None = Field(default=None, ge=0, le=90)

    @model_validator(mode="after")
    def _exactly_one(self) -> Delay:
        supplied = [
            v
            for v in (self.working_minutes, self.working_hours, self.working_days)
            if v is not None
        ]
        if len(supplied) > 1:
            raise ValueError("Specify exactly one delay unit.")
        return self

    def total_working_minutes(self, minutes_per_working_day: int) -> int:
        if self.working_minutes is not None:
            return self.working_minutes
        if self.working_hours is not None:
            return self.working_hours * 60
        if self.working_days is not None:
            return self.working_days * minutes_per_working_day
        return 0


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["create_task", "assign_owner", "notify_owner", "notify_manager"]
    channel: Literal["in_app", "email"] = "in_app"
    # Title/body templates are plain text with named placeholders resolved by
    # the engine. No template language is executed.
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=2000)
    task_kind: str | None = Field(default=None, max_length=20)
    due_in: Delay | None = None

    @model_validator(mode="after")
    def _channel_available(self) -> Action:
        if self.channel == "email":
            # External sending stays disabled until the connector and the
            # authority to send exist (CRM06). Rejecting it at validation time
            # means a rule cannot be saved in a state that would silently fail.
            raise ValueError(
                "The email channel is not available until a connector is "
                "configured and authorised (Release 2)."
            )
        return self


class Limits(BaseModel):
    """Loop and frequency controls (section 8.3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_actions: Annotated[int, Field(ge=1, le=3)] = 3
    cooldown_minutes: Annotated[int, Field(ge=0, le=60 * 24 * 7)] = 60
    max_chain_depth: Annotated[int, Field(ge=1, le=3)] = 3


class RuleConfig(BaseModel):
    """The complete stored rule shape."""

    model_config = ConfigDict(extra="forbid")

    template: str = Field(max_length=40)
    version: Annotated[int, Field(ge=1)] = 1
    trigger: str
    conditions: list[Condition] = Field(default_factory=list, max_length=10)
    delay: Delay | None = None
    actions: list[Action] = Field(min_length=1, max_length=3)
    limits: Limits = Field(default_factory=Limits)

    @field_validator("trigger")
    @classmethod
    def _known_trigger(cls, value: str) -> str:
        if value not in ALLOWED_TRIGGERS:
            raise ValueError(f"Unknown trigger {value!r}. Allowed: {sorted(ALLOWED_TRIGGERS)}")
        return value

    @model_validator(mode="after")
    def _within_action_limit(self) -> RuleConfig:
        if len(self.actions) > self.limits.max_actions:
            raise ValueError("The rule declares more actions than its own max_actions limit.")
        return self
