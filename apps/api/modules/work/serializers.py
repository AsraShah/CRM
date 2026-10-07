"""Work serializers (CRM05)."""

from __future__ import annotations

from rest_framework import serializers

from modules.automation.models import Notification
from modules.common import limits
from modules.work.models import Task, TaskKind


class TaskSerializer(serializers.ModelSerializer):
    owner_email = serializers.EmailField(source="owner.email", read_only=True, default=None)
    contact_name = serializers.CharField(
        source="contact.display_name", read_only=True, default=None
    )
    opportunity_service = serializers.CharField(
        source="opportunity.service", read_only=True, default=None
    )
    # True when the deadline has passed and the task is still open. Computed
    # server-side so every client agrees on what "overdue" means.
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "title",
            "description",
            "kind",
            "status",
            "owner",
            "owner_email",
            "origin",
            "due_at",
            # Kept alongside due_at so the interface can show that a deadline
            # moved, rather than only its latest value (CRM05).
            "original_due_at",
            "reschedule_count",
            "is_overdue",
            "contact",
            "contact_name",
            "opportunity",
            "opportunity_service",
            "lead",
            "project",
            "milestone",
            "completed_at",
            "outcome",
            "stop_reason",
            "blocked_reason",
            "version",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "origin",
            "original_due_at",
            "reschedule_count",
            "completed_at",
            "version",
            "created_at",
        ]

    def get_is_overdue(self, obj: Task) -> bool:
        from django.utils import timezone

        return obj.is_open and obj.due_at < timezone.now()


class TaskCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=300)
    due_at = serializers.DateTimeField()
    owner = serializers.UUIDField(required=False, allow_null=True)
    kind = serializers.ChoiceField(
        choices=TaskKind.choices, required=False, default=TaskKind.FOLLOW_UP
    )
    description = limits.text(limits.LONG_TEXT, required=False)
    contact = serializers.UUIDField(required=False, allow_null=True)
    opportunity = serializers.UUIDField(required=False, allow_null=True)
    lead = serializers.UUIDField(required=False, allow_null=True)
    project = serializers.UUIDField(required=False, allow_null=True)
    milestone = serializers.UUIDField(required=False, allow_null=True)


class TaskCompleteSerializer(serializers.Serializer):
    outcome = limits.text(limits.LONG_TEXT, required=False)
    expected_version = serializers.IntegerField(min_value=1)
    # Either a next action or an explicit stop reason. The service rejects
    # neither being supplied, which is what keeps next-action coverage high.
    next_action_title = serializers.CharField(max_length=300, required=False, allow_blank=True)
    next_action_due_at = serializers.DateTimeField(required=False, allow_null=True)
    stop_reason = limits.text(limits.REASON, required=False)


class TaskRescheduleSerializer(serializers.Serializer):
    new_due_at = serializers.DateTimeField()
    reason = serializers.CharField(max_length=1000)
    expected_version = serializers.IntegerField(min_value=1)


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "body",
            # Every alert explains why it appeared (SVX-PRD-001 section 7.2).
            "cause",
            "state",
            "entity_type",
            "entity_id",
            "acknowledged_at",
            "created_at",
        ]
        read_only_fields = fields


class TodaySerializer(serializers.Serializer):
    """Everything the Today screen needs, in one request.

    A single round trip matters here: this is the first screen of the day and
    four sequential fetches would be visibly slower on a modest connection.
    """

    overdue = TaskSerializer(many=True)
    due_now = TaskSerializer(many=True)
    upcoming = TaskSerializer(many=True)
    blocked = TaskSerializer(many=True)
    alerts = NotificationSerializer(many=True)
    server_time = serializers.DateTimeField()
    workspace_time_zone = serializers.CharField()
