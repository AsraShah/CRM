"""Ticket serializers (CRM09)."""

from __future__ import annotations

from rest_framework import serializers

from modules.common import limits
from modules.support.models import (
    CommentVisibility,
    Ticket,
    TicketComment,
    TicketOrigin,
    TicketPriority,
    TicketState,
    TicketStateHistory,
)


class TicketCommentSerializer(serializers.ModelSerializer):
    author_email = serializers.EmailField(source="author.email", read_only=True, default=None)

    class Meta:
        model = TicketComment
        fields = [
            "id",
            "ticket",
            "author",
            "author_email",
            "body",
            # Always exposed. The interface must render an internal note
            # unmistakably differently from one the client can read (CRM09).
            "visibility",
            "corrects",
            "created_at",
        ]
        read_only_fields = ["id", "ticket", "author", "corrects", "created_at"]


class TicketCommentCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=10000)
    # Defaults to internal, and stays internal unless deliberately changed.
    visibility = serializers.ChoiceField(
        choices=CommentVisibility.choices, default=CommentVisibility.INTERNAL
    )


class TicketStateHistorySerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = TicketStateHistory
        fields = [
            "id",
            "from_state",
            "to_state",
            "actor",
            "actor_email",
            "changed_at",
            "reason",
            # What was previously claimed to be fixed, preserved through a
            # reopen (CRM09).
            "superseded_resolution",
        ]
        read_only_fields = fields


class TicketSerializer(serializers.ModelSerializer):
    owner_email = serializers.EmailField(source="owner.email", read_only=True, default=None)
    client_name = serializers.CharField(source="client.display_name", read_only=True, default=None)
    is_open = serializers.BooleanField(read_only=True)
    waiting_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            "id",
            "title",
            "description",
            "origin",
            "priority",
            "state",
            "is_open",
            "client",
            "client_name",
            "project",
            "milestone",
            "owner",
            "owner_email",
            "raised_by",
            "waiting_reason",
            "waiting_next_owner",
            "waiting_review_at",
            "waiting_overdue",
            "next_action",
            "resolution_note",
            "closure_test_result",
            "resolved_at",
            "closed_at",
            # Surfaced so a ticket that keeps coming back is visible as such.
            "reopen_count",
            "version",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "is_open",
            "raised_by",
            "resolved_at",
            "closed_at",
            "reopen_count",
            "version",
            "created_at",
        ]

    def get_waiting_overdue(self, obj: Ticket) -> bool:
        from django.utils import timezone

        if obj.waiting_review_at is None:
            return False
        return obj.is_open and obj.waiting_review_at < timezone.now()


class TicketCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=300)
    description = limits.text(limits.LONG_TEXT, required=False)
    origin = serializers.ChoiceField(
        choices=TicketOrigin.choices, default=TicketOrigin.CLIENT_REQUEST
    )
    priority = serializers.ChoiceField(
        choices=TicketPriority.choices, default=TicketPriority.NORMAL
    )
    client = serializers.UUIDField(required=False, allow_null=True)
    project = serializers.UUIDField(required=False, allow_null=True)
    milestone = serializers.UUIDField(required=False, allow_null=True)
    owner_id = serializers.UUIDField(required=False, allow_null=True)


class TicketTransitionSerializer(serializers.Serializer):
    target_state = serializers.ChoiceField(choices=TicketState.choices)
    expected_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    # Required when moving to a waiting state: without them a ticket waits
    # indefinitely and nobody notices.
    waiting_reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    waiting_next_owner_id = serializers.UUIDField(required=False, allow_null=True)
    waiting_review_at = serializers.DateTimeField(required=False, allow_null=True)

    # Required when resolving. Both of them.
    resolution_note = serializers.CharField(max_length=5000, required=False, allow_blank=True)
    closure_test_result = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class TicketNextActionSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    # Blank clears it; a ticket may genuinely have nothing pending.
    next_action = serializers.CharField(max_length=300, allow_blank=True)
