"""Client, project and milestone serializers (CRM07, CRM08)."""

from __future__ import annotations

from rest_framework import serializers

from modules.crm.models import Client
from modules.work.models import Milestone, MilestoneStatus, Project, ScopeChange


class ClientSerializer(serializers.ModelSerializer):
    contact_name = serializers.CharField(source="contact.display_name", read_only=True)
    delivery_owner_email = serializers.EmailField(
        source="delivery_owner.email", read_only=True, default=None
    )
    awaiting_handover = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = [
            "id",
            "display_name",
            "contact",
            "contact_name",
            "company",
            "originating_opportunity",
            "delivery_owner",
            "delivery_owner_email",
            "status",
            # Carried across at conversion so delivery never has to ask sales
            # for another spreadsheet (SVX-PRD-001 section 7.2).
            "accepted_scope",
            "exclusions",
            "commercial_reference",
            "promised_start_on",
            "promised_end_on",
            "handover_accepted_at",
            "handover_returned_at",
            "handover_returned_reason",
            "awaiting_handover",
            "version",
            "created_at",
        ]
        read_only_fields = fields

    def get_awaiting_handover(self, obj: Client) -> bool:
        return obj.status == "pending_handover"


class ConvertOpportunitySerializer(serializers.Serializer):
    """Everything the handover must carry (CRM07)."""

    expected_version = serializers.IntegerField(min_value=1)
    accepted_scope_evidence = serializers.CharField(max_length=5000)
    commercial_reference = serializers.CharField(max_length=500)
    # Required. A handover with no named delivery owner is not a handover, and
    # the service rejects a null with a message saying so.
    delivery_owner_id = serializers.UUIDField()
    exclusions = serializers.CharField(max_length=5000, required=False, allow_blank=True)
    promised_start_on = serializers.DateField(required=False, allow_null=True)
    promised_end_on = serializers.DateField(required=False, allow_null=True)
    template_key = serializers.CharField(
        max_length=64, required=False, default="standard_agency_onboarding"
    )

    def validate(self, attrs):
        start, end = attrs.get("promised_start_on"), attrs.get("promised_end_on")
        # A delivery promise that ends before it starts is a typo that would
        # otherwise reach the client record and every overdue calculation.
        if start and end and end < start:
            raise serializers.ValidationError(
                {"promised_end_on": ["The promised end date cannot be before the start date."]}
            )
        return attrs


class HandoverDecisionSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    note = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class HandoverReturnSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    # Mandatory: a bare rejection leaves sales guessing what to fix.
    missing_information = serializers.CharField(max_length=2000)


class MilestoneSerializer(serializers.ModelSerializer):
    owner_email = serializers.EmailField(source="owner.email", read_only=True, default=None)
    unmet_dependencies = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Milestone
        fields = [
            "id",
            "project",
            "name",
            "description",
            "sequence",
            "status",
            "owner",
            "owner_email",
            "due_on",
            "is_overdue",
            # Submission and acceptance are separate facts with separate actors
            # and separate timestamps (CRM08).
            "submitted_at",
            "submitted_by",
            "evidence",
            "accepted_at",
            "accepted_by",
            "acceptance_note",
            "blocked_reason",
            "blocked_next_owner",
            "dependency_override_reason",
            "cancelled_reason",
            "cancellation_impact",
            "unmet_dependencies",
            "version",
        ]
        read_only_fields = [
            "id",
            "submitted_at",
            "submitted_by",
            "accepted_at",
            "accepted_by",
            "version",
        ]

    def get_unmet_dependencies(self, obj: Milestone) -> list[dict]:
        # Surfaced so the interface can explain *why* acceptance is unavailable
        # rather than just disabling the button.
        #
        # `obj.dependencies` yields MilestoneDependency rows, so the milestone
        # being waited on is `link.depends_on` -- reading name and status off
        # the link itself raises, and does so only once a dependency exists,
        # which is precisely when this field matters.
        return [
            {
                "id": str(link.depends_on.id),
                "name": link.depends_on.name,
                "status": link.depends_on.status,
            }
            for link in obj.dependencies.select_related("depends_on").all()
            if link.depends_on.status != MilestoneStatus.ACCEPTED
        ]

    def get_is_overdue(self, obj: Milestone) -> bool:
        from django.utils import timezone

        if obj.due_on is None:
            return False
        if obj.status in {MilestoneStatus.ACCEPTED, MilestoneStatus.CANCELLED}:
            return False
        return obj.due_on < timezone.now().date()


class ProjectSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.display_name", read_only=True)
    milestones = MilestoneSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "client",
            "client_name",
            "originating_opportunity",
            "name",
            "description",
            "status",
            "owner",
            "starts_on",
            "due_on",
            "completed_at",
            "milestones",
            "version",
            "created_at",
        ]
        read_only_fields = fields


class MilestoneSubmitSerializer(serializers.Serializer):
    evidence = serializers.CharField(max_length=5000)
    expected_version = serializers.IntegerField(min_value=1)


class MilestoneAcceptSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    note = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    # An override is a manager decision and must state its reason; the service
    # enforces both.
    override_dependencies = serializers.BooleanField(default=False)
    override_reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class MilestoneReopenSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=2000)


class MilestoneBlockSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    blocked_reason = serializers.CharField(max_length=2000)
    next_owner_id = serializers.UUIDField()


class ScopeChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScopeChange
        fields = [
            "id",
            "project",
            "milestone",
            # Recorded distinctly: a defect is work we already owed, a scope
            # change is work we agreed to add (CRM08).
            "kind",
            "description",
            "commercial_impact",
            "requested_by",
            "recorded_at",
        ]
        read_only_fields = ["id", "requested_by", "recorded_at"]


class ScopeChangeCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["scope_change", "defect"])
    description = serializers.CharField(max_length=5000)
    commercial_impact = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    milestone_id = serializers.UUIDField(required=False, allow_null=True)


class MilestoneVersionSerializer(serializers.Serializer):
    """For transitions that need nothing but optimistic-concurrency safety."""

    expected_version = serializers.IntegerField(min_value=1)


class MilestoneCancelSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=2000)
    impact = serializers.CharField(max_length=2000)


class MilestoneReturnSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=2000)


class MilestoneDependencySerializer(serializers.Serializer):
    """The milestone this one waits on. Validated as an id before any lookup."""

    depends_on = serializers.UUIDField()
