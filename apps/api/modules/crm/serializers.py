"""CRM serializers (SVX-TECH-001 section 6.1).

Serializers validate request shape. They never decide business outcomes -- an
evidence label, a stage requirement or an ownership rule is settled by the
service, because the service is also reachable from a worker or a test.
"""

from __future__ import annotations

from rest_framework import serializers

from modules.common import limits
from modules.crm.models import (
    Activity,
    ActivityKind,
    Contact,
    ImportBatch,
    Lead,
    LeadSource,
    LeadStatus,
    Opportunity,
    OpportunityStage,
    StageHistory,
)


class ContactSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True, default=None)

    class Meta:
        model = Contact
        fields = [
            "id",
            "display_name",
            "company",
            "company_name",
            "email",
            "phone",
            "phone_e164",
            "job_title",
            "source",
            "source_reference",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "phone_e164", "version", "created_at", "updated_at"]


class ContactCreateSerializer(serializers.Serializer):
    display_name = limits.text(limits.NAME)
    email = limits.email(required=False)
    phone = limits.phone()
    # Required for phone normalisation. The server will not guess a country:
    # an unparseable number is kept as written and excluded from matching.
    default_country = limits.country()
    job_title = limits.text(limits.JOB_TITLE, required=False)
    source = serializers.ChoiceField(
        choices=LeadSource.choices, required=False, default=LeadSource.UNKNOWN
    )
    source_reference = limits.text(limits.REFERENCE, required=False)


class LeadSerializer(serializers.ModelSerializer):
    contact_name = serializers.CharField(source="contact.display_name", read_only=True)
    owner_email = serializers.EmailField(source="owner.email", read_only=True, default=None)

    class Meta:
        model = Lead
        fields = [
            "id",
            "contact",
            "contact_name",
            "owner",
            "owner_email",
            "source",
            "source_reference",
            "status",
            "qualified_at",
            "nurture_review_at",
            "disqualified_reason",
            "need",
            "fit",
            "notes",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "qualified_at", "version", "created_at", "updated_at"]


class LeadCreateSerializer(serializers.Serializer):
    contact = serializers.UUIDField()
    owner = serializers.UUIDField(required=False, allow_null=True)
    source = serializers.ChoiceField(
        choices=LeadSource.choices, required=False, default=LeadSource.UNKNOWN
    )
    source_reference = limits.text(limits.REFERENCE, required=False)
    notes = limits.text(limits.LONG_TEXT, required=False)


class LeadStatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=LeadStatus.choices)
    # Never optional: without it a stale client could overwrite a colleague's
    # change (section 6.2).
    expected_version = serializers.IntegerField(min_value=1)
    nurture_review_at = serializers.DateTimeField(required=False, allow_null=True)
    disqualified_reason = limits.text(limits.REASON, required=False)
    # What qualification established. Required when moving to qualified.
    need = limits.text(limits.REASON, required=False)
    fit = limits.text(limits.REASON, required=False)


class LeadAssignSerializer(serializers.Serializer):
    owner = serializers.UUIDField()
    expected_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class StageHistorySerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = StageHistory
        fields = [
            "id",
            "from_stage",
            "to_stage",
            "actor",
            "actor_email",
            "changed_at",
            "reason",
        ]
        read_only_fields = fields


class OpportunitySerializer(serializers.ModelSerializer):
    contact_name = serializers.CharField(source="contact.display_name", read_only=True)
    owner_email = serializers.EmailField(source="owner.email", read_only=True, default=None)
    # How long this deal has sat in its current stage, which the pipeline shows
    # instead of making the user compute it (CRM03).
    days_in_stage = serializers.SerializerMethodField()

    class Meta:
        model = Opportunity
        fields = [
            "id",
            "contact",
            "contact_name",
            "lead",
            "service",
            "stage",
            "owner",
            "owner_email",
            "amount",
            "currency",
            "expected_close_on",
            "next_action_at",
            "stage_entered_at",
            "last_activity_at",
            "days_in_stage",
            "scope_reference",
            "commercial_reference",
            "closed_at",
            "lost_reason",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "next_action_at",
            "stage_entered_at",
            "last_activity_at",
            "closed_at",
            "version",
            "created_at",
            "updated_at",
        ]

    def get_days_in_stage(self, obj: Opportunity) -> int | None:
        if obj.stage_entered_at is None:
            return None
        from django.utils import timezone

        return (timezone.now() - obj.stage_entered_at).days


class OpportunityCreateSerializer(serializers.Serializer):
    contact = serializers.UUIDField()
    service = limits.text(limits.NAME)
    owner = serializers.UUIDField(required=False, allow_null=True)
    lead = serializers.UUIDField(required=False, allow_null=True)
    # Null means the value is unknown. It is never defaulted to zero, which
    # would silently join pipeline totals as a real figure.
    amount = limits.money(required=False, allow_null=True)
    currency = limits.currency(required=False)
    expected_close_on = serializers.DateField(required=False, allow_null=True)


class OpportunityTransitionSerializer(serializers.Serializer):
    target_stage = serializers.ChoiceField(choices=OpportunityStage.choices)
    expected_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    scope_reference = limits.text(limits.REFERENCE, required=False)
    lost_reason = limits.text(limits.REASON, required=False)


class ActivitySerializer(serializers.ModelSerializer):
    author_email = serializers.EmailField(source="author.email", read_only=True, default=None)
    contact_name = serializers.CharField(source="contact.display_name", read_only=True)

    class Meta:
        model = Activity
        fields = [
            "id",
            "contact",
            "contact_name",
            "opportunity",
            "lead",
            "kind",
            "outcome",
            "occurred_at",
            "recorded_at",
            "author",
            "author_email",
            # Exposed on every activity so the interface can label a claim as a
            # claim. A manual entry is never shown as a verified call (CRM04).
            "evidence_type",
            "provider_reference",
            "source_url",
            "corrects",
            "follow_up_task",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "recorded_at",
            "author",
            "evidence_type",
            "provider_reference",
            "corrects",
            "created_at",
        ]


class ActivityCreateSerializer(serializers.Serializer):
    contact = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=ActivityKind.choices)
    occurred_at = serializers.DateTimeField()
    outcome = limits.text(limits.LONG_TEXT, required=False)
    opportunity = serializers.UUIDField(required=False, allow_null=True)
    lead = serializers.UUIDField(required=False, allow_null=True)
    source_url = serializers.URLField(max_length=200, required=False, allow_blank=True)
    # The next action agreed in this interaction, scheduled as a task.
    follow_up_title = limits.text(limits.TITLE, required=False)
    follow_up_due_at = serializers.DateTimeField(required=False, allow_null=True)
    # Deliberately absent: evidence_type. A client cannot declare its own entry
    # provider-confirmed.


class ActivityCorrectionSerializer(serializers.Serializer):
    outcome = limits.text(limits.LONG_TEXT)
    reason = serializers.CharField(max_length=1000)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class ImportBatchSerializer(serializers.ModelSerializer):
    counts_reconcile = serializers.BooleanField(read_only=True)

    class Meta:
        model = ImportBatch
        fields = [
            "id",
            "original_filename",
            "byte_size",
            "status",
            "detected_headers",
            "column_mapping",
            "mapping_version",
            "default_country",
            "duplicate_policy",
            "total_rows",
            "created_count",
            "duplicate_count",
            "invalid_count",
            "skipped_count",
            "counts_reconcile",
            "committed_at",
            "completed_at",
            "failure_reason",
            "version",
            "created_at",
        ]
        read_only_fields = fields


class ImportPreviewRequestSerializer(serializers.Serializer):
    # {"Spreadsheet column": "target_field"}
    column_mapping = serializers.DictField(
        child=serializers.CharField(max_length=64, allow_blank=True), allow_empty=False
    )
    default_country = limits.country()
    duplicate_policy = serializers.ChoiceField(choices=["skip", "update"], default="skip")

    def validate_column_mapping(self, value: dict[str, str]) -> dict[str, str]:
        # A real spreadsheet has tens of columns; hundreds is a malformed file
        # or an attempt to make the preview do unbounded work.
        if len(value) > 200:
            raise serializers.ValidationError("Map at most 200 columns.")
        if any(len(header) > limits.NAME for header in value):
            raise serializers.ValidationError(
                f"Column names may be at most {limits.NAME} characters."
            )
        return value


class ImportRowAssessmentSerializer(serializers.Serializer):
    row_number = serializers.IntegerField()
    state = serializers.CharField()
    errors = serializers.ListField(child=serializers.CharField())
    raw = serializers.DictField()


class ImportPreviewResponseSerializer(serializers.Serializer):
    batch = ImportBatchSerializer()
    total_rows = serializers.IntegerField()
    created = serializers.IntegerField()
    duplicate = serializers.IntegerField()
    invalid = serializers.IntegerField()
    skipped = serializers.IntegerField()
    # Totals must account for every input row. The interface blocks commit when
    # this is false rather than importing a batch that has lost rows.
    reconciles = serializers.BooleanField()
    # A bounded sample; the full detail is in the downloadable error file.
    sample_rows = ImportRowAssessmentSerializer(many=True)
    preview_hash = serializers.CharField()


class ImportCommitSerializer(serializers.Serializer):
    preview_hash = serializers.CharField(max_length=64)
    expected_version = serializers.IntegerField(min_value=1)


# ---------------------------------------------------------------------------
# Edits. Every field is optional; only the ones sent are changed.
# ---------------------------------------------------------------------------


class ContactUpdateSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    display_name = serializers.CharField(max_length=limits.NAME, required=False)
    email = limits.email(required=False)
    phone = limits.phone()
    job_title = limits.text(limits.JOB_TITLE, required=False)
    source_reference = limits.text(limits.REFERENCE, required=False)
    # Context for normalising a changed phone number; not stored.
    default_country = limits.country()


class LeadUpdateSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    source = serializers.ChoiceField(choices=LeadSource.choices, required=False)
    source_reference = limits.text(limits.REFERENCE, required=False)
    notes = limits.text(limits.LONG_TEXT, required=False)
    need = limits.text(limits.REASON, required=False)
    fit = limits.text(limits.REASON, required=False)


class OpportunityUpdateSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    service = serializers.CharField(max_length=limits.NAME, required=False)
    # Null clears the value back to unknown. It is never treated as zero.
    amount = limits.money(required=False, allow_null=True)
    currency = limits.currency(required=False)
    expected_close_on = serializers.DateField(required=False, allow_null=True)
    scope_reference = limits.text(limits.REFERENCE, required=False)
    commercial_reference = limits.text(limits.REFERENCE, required=False)


class ContactMergeSerializer(serializers.Serializer):
    """Merge ``duplicate_id`` into the contact in the URL, which survives."""

    duplicate_id = serializers.UUIDField()
    survivor_version = serializers.IntegerField(min_value=1)
    duplicate_version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=1000)
