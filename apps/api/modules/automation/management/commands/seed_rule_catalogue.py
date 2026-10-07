"""Install the initial rule catalogue (SVX-PRD-001 section 5.2).

Every timing here is a *proposed default* awaiting validation with ScaleVexo's
sales and delivery managers at G0. They are stored as editable rule versions
precisely so that changing one is a configuration decision, not a code change.

Rules are created **disabled**. An administrator reviews the simulation output
and enables each one deliberately -- the first thing a new rule should not do is
start notifying people.

All eight rules from the catalogue are installed. A08 (repeated rescheduling)
is detected by the exception sweep rather than by a rule action, because its
required outcome is "raise a reviewable exception, not a disciplinary decision"
— and an exception is not something the rule action set can express.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from modules.automation.models import RuleDefinition, RuleTemplate
from modules.automation.schemas import RuleConfig
from modules.common.tenancy import workspace_context
from modules.identity.models import Workspace

CATALOGUE: list[dict] = [
    {
        "template": RuleTemplate.UNASSIGNED_LEAD,
        "name": "A01 Unassigned lead",
        "explanation": (
            "A new lead has had no owner for 30 working minutes. Nobody is "
            "responsible for contacting this person yet."
        ),
        "config": {
            "trigger": "lead.created",
            "conditions": [{"field": "lead.owner_id", "op": "is_empty"}],
            "delay": {"working_minutes": 30},
            "actions": [
                {
                    "type": "notify_manager",
                    "channel": "in_app",
                    "title": "Lead is still unassigned",
                }
            ],
            # One unresolved alert per lead: a second reminder about the same
            # neglected lead adds noise, not information.
            "limits": {"max_actions": 1, "cooldown_minutes": 240},
        },
    },
    {
        "template": RuleTemplate.MISSING_NEXT_ACTION,
        "name": "A02 Open deal without a next action",
        "explanation": (
            "This deal is open but nothing is scheduled to happen next, so it "
            "will drift unless somebody decides the next step."
        ),
        "config": {
            "trigger": "opportunity.stage_changed",
            "conditions": [
                {"field": "opportunity.next_action_at", "op": "is_empty"},
                {
                    "field": "opportunity.stage",
                    "op": "in",
                    "value": ["discovery", "qualified", "proposal", "negotiation"],
                },
            ],
            "delay": {"working_hours": 4},
            "actions": [
                {
                    "type": "create_task",
                    "title": "Decide the next action for this deal",
                    "due_in": {"working_hours": 4},
                }
            ],
            "limits": {"max_actions": 1, "cooldown_minutes": 480},
        },
    },
    {
        "template": RuleTemplate.OVERDUE_FOLLOWUP,
        "name": "A03 Overdue follow-up",
        "explanation": ("A follow-up passed its due time by two working hours and is still open."),
        "config": {
            "trigger": "task.due",
            "conditions": [{"field": "task.status", "op": "eq", "value": "open"}],
            "delay": {"working_hours": 2},
            "actions": [
                {
                    "type": "notify_owner",
                    "channel": "in_app",
                    "title": "Your follow-up is overdue",
                }
            ],
            "limits": {"max_actions": 1, "cooldown_minutes": 60},
        },
    },
    {
        "template": RuleTemplate.STALE_DEAL,
        "name": "A04 No recent activity on an open deal",
        "explanation": (
            "No meaningful activity has been recorded on this open deal for "
            "three working days. This asks the owner for an update; it does "
            "not mark the deal lost."
        ),
        "config": {
            "trigger": "opportunity.stage_changed",
            "conditions": [
                {
                    "field": "opportunity.stage",
                    "op": "in",
                    "value": ["discovery", "qualified", "proposal", "negotiation"],
                }
            ],
            "delay": {"working_days": 3},
            "actions": [
                {
                    "type": "create_task",
                    "title": "Update this deal or record why it is paused",
                    "due_in": {"working_hours": 4},
                }
            ],
            "limits": {"max_actions": 1, "cooldown_minutes": 1440},
        },
    },
    {
        "template": RuleTemplate.DEAL_WON,
        "name": "A05 Deal won, onboarding awaiting acceptance",
        "explanation": (
            "This deal was won and an onboarding project was created, but "
            "delivery has not yet accepted the handover."
        ),
        "config": {
            "trigger": "client.handover_requested",
            "conditions": [{"field": "client.status", "op": "eq", "value": "pending_handover"}],
            "delay": {"working_hours": 4},
            "actions": [
                {
                    "type": "notify_manager",
                    "channel": "in_app",
                    "title": "A handover is waiting for delivery acceptance",
                }
            ],
            "limits": {"max_actions": 1, "cooldown_minutes": 480},
        },
    },
    {
        "template": RuleTemplate.MILESTONE_OVERDUE,
        "name": "A06 Milestone blocked",
        "explanation": (
            "A milestone is blocked. The delivery manager needs to see which "
            "client commitments this affects."
        ),
        "config": {
            "trigger": "milestone.blocked",
            "conditions": [{"field": "milestone.status", "op": "eq", "value": "blocked"}],
            "actions": [
                {
                    "type": "notify_manager",
                    "channel": "in_app",
                    "title": "Delivery is blocked",
                }
            ],
            "limits": {"max_actions": 1, "cooldown_minutes": 240},
        },
    },
    {
        "template": RuleTemplate.CRITICAL_TICKET,
        "name": "A07 Critical ticket raised",
        "explanation": "A ticket was raised at critical priority.",
        "config": {
            "trigger": "ticket.created",
            "conditions": [{"field": "ticket.priority", "op": "eq", "value": "critical"}],
            # No delay. A critical ticket that waits two working hours for its
            # alert was not treated as critical.
            "actions": [
                {
                    "type": "notify_manager",
                    "channel": "in_app",
                    "title": "Critical ticket raised",
                }
            ],
            "limits": {"max_actions": 1, "cooldown_minutes": 0},
        },
    },
]


class Command(BaseCommand):
    help = "Install or refresh the Release 1 rule catalogue for one workspace."

    def add_arguments(self, parser) -> None:
        parser.add_argument("workspace_slug", help="Slug of the target workspace.")
        parser.add_argument(
            "--enable",
            action="store_true",
            help=(
                "Enable the rules immediately. Omit this on a live workspace: "
                "review each simulation first."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        slug = options["workspace_slug"]
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            raise CommandError(f"No workspace with slug {slug!r}.")

        created, updated = 0, 0
        with workspace_context(workspace.id):
            for entry in CATALOGUE:
                config = RuleConfig.model_validate(
                    {
                        "template": entry["template"],
                        "version": 1,
                        **entry["config"],
                    }
                )
                rule, was_created = RuleDefinition.objects.update_or_create(
                    workspace=workspace,
                    template=entry["template"],
                    rule_version=config.version,
                    defaults={
                        "name": entry["name"],
                        "explanation": entry["explanation"],
                        "trigger": config.trigger,
                        "conditions": [c.model_dump() for c in config.conditions],
                        "actions": [a.model_dump(exclude_none=True) for a in config.actions],
                        "delay": config.delay.model_dump(exclude_none=True) if config.delay else {},
                        "limits": config.limits.model_dump(),
                        "enabled": options["enable"],
                    },
                )
                created += int(was_created)
                updated += int(not was_created)

        state = "enabled" if options["enable"] else "disabled (review before enabling)"
        self.stdout.write(
            self.style.SUCCESS(
                f"Rule catalogue for {workspace.name}: "
                f"{created} created, {updated} updated, {state}."
            )
        )
