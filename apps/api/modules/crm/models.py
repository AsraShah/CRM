"""Contacts, leads, opportunities and activities (CRM02-CRM04).

Key modelling decisions from SVX-PRD-001 section 2 and SVX-TECH-001 section 4:

* The person or company being contacted is separate from each potential sale.
  A Contact can carry several Opportunities over time without being recreated.
* Lead *status* describes qualification. Opportunity *stage* describes one
  particular sale. The two are kept apart in storage, in the API and in reports.
* Null means unknown. An opportunity with no agreed value stores NULL, never 0 --
  zero would silently join pipeline sums as a real figure.
* Raw imported values are retained alongside normalised ones, so a normalisation
  bug can be diagnosed and reversed.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from modules.common.models import (
    MONEY_DECIMAL_PLACES,
    MONEY_MAX_DIGITS,
    WorkspaceScopedModel,
)


class LeadSource(models.TextChoices):
    REFERRAL = "referral", "Referral"
    WEBSITE = "website", "Website"
    LINKEDIN = "linkedin", "LinkedIn"
    OUTBOUND = "outbound", "Outbound prospecting"
    EVENT = "event", "Event"
    IMPORT = "import", "Spreadsheet import"
    OTHER = "other", "Other"
    UNKNOWN = "unknown", "Unknown"


class LeadStatus(models.TextChoices):
    """Qualification state (SVX-PRD-001 section 5)."""

    NEW = "new", "New"
    ASSIGNED = "assigned", "Assigned"
    CONTACTING = "contacting", "Contacting"
    QUALIFIED = "qualified", "Qualified"
    NURTURE = "nurture", "Nurture"
    DISQUALIFIED = "disqualified", "Disqualified"


class OpportunityStage(models.TextChoices):
    """Stage of one particular sale (SVX-PRD-001 section 5)."""

    DISCOVERY = "discovery", "Discovery"
    QUALIFIED = "qualified", "Qualified"
    PROPOSAL = "proposal", "Proposal"
    NEGOTIATION = "negotiation", "Negotiation"
    WON = "won", "Won"
    LOST = "lost", "Lost"


OPEN_STAGES = frozenset(
    {
        OpportunityStage.DISCOVERY,
        OpportunityStage.QUALIFIED,
        OpportunityStage.PROPOSAL,
        OpportunityStage.NEGOTIATION,
    }
)
CLOSED_STAGES = frozenset({OpportunityStage.WON, OpportunityStage.LOST})


class ActivityKind(models.TextChoices):
    CALL = "call", "Call"
    EMAIL = "email", "Email"
    MEETING = "meeting", "Meeting"
    LINKEDIN = "linkedin", "LinkedIn touch"
    NOTE = "note", "Note"


class EvidenceType(models.TextChoices):
    """How much we actually know about whether an activity happened (CRM04).

    A manually entered "call completed" is a claim by the employee. Only an
    event carrying a provider reference may be labelled provider-confirmed.
    These are separate enumerated values so that a report can never quietly
    treat one as the other.
    """

    SELF_REPORTED = "self_reported", "Self-reported by the user"
    PROVIDER_CONFIRMED = "provider_confirmed", "Confirmed by a connected provider"


class Company(WorkspaceScopedModel):
    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200, db_index=True)
    website = models.URLField(blank=True, default="")
    source_reference = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "crm_company"
        indexes = [models.Index(fields=["workspace", "normalized_name"])]

    def __str__(self) -> str:
        return self.name


class Contact(WorkspaceScopedModel):
    """A person. Deduplication prefers an exact normalised email or phone."""

    display_name = models.CharField(max_length=200)
    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="contacts"
    )
    email = models.EmailField(blank=True, default="")
    # Lower-cased for matching. Blank (not NULL) when unknown, so that the
    # partial unique index below simply does not apply.
    normalized_email = models.CharField(max_length=320, blank=True, default="")
    phone = models.CharField(max_length=40, blank=True, default="")
    # E.164 only when parsing succeeded with explicit country context. A number
    # we could not confidently parse stays in `phone` and is not used for
    # matching -- guessing a country would merge unrelated people.
    phone_e164 = models.CharField(max_length=20, blank=True, default="")
    job_title = models.CharField(max_length=150, blank=True, default="")
    source = models.CharField(max_length=32, choices=LeadSource.choices, default=LeadSource.UNKNOWN)
    source_reference = models.CharField(max_length=500, blank=True, default="")
    # The row exactly as imported, for diagnosing a normalisation defect.
    raw_import_values = models.JSONField(default=dict, blank=True)
    merged_into = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="merged_from"
    )

    class Meta:
        db_table = "crm_contact"
        constraints = [
            # Partial unique indexes: they apply only where a value exists, so
            # many contacts may legitimately have no email at all.
            models.UniqueConstraint(
                fields=["workspace", "normalized_email"],
                condition=models.Q(normalized_email__gt="", deleted_at__isnull=True),
                name="uniq_contact_email_per_workspace",
            ),
            models.UniqueConstraint(
                fields=["workspace", "phone_e164"],
                condition=models.Q(phone_e164__gt="", deleted_at__isnull=True),
                name="uniq_contact_phone_per_workspace",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "display_name"]),
            models.Index(fields=["workspace", "normalized_email"]),
            models.Index(fields=["workspace", "phone_e164"]),
        ]

    def __str__(self) -> str:
        return self.display_name


class Lead(WorkspaceScopedModel):
    """Qualification of a contact. Distinct from any resulting sale."""

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="leads")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_leads",
    )
    source = models.CharField(max_length=32, choices=LeadSource.choices, default=LeadSource.UNKNOWN)
    source_reference = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=20, choices=LeadStatus.choices, default=LeadStatus.NEW)
    qualified_at = models.DateTimeField(null=True, blank=True)
    # Nurture requires a review date; disqualification requires a reason
    # (SVX-PRD-001 section 5).
    nurture_review_at = models.DateTimeField(null=True, blank=True)
    disqualified_reason = models.TextField(blank=True, default="")
    # What qualification established (SVX-PRD-001 section 5): the problem the
    # prospect needs solved, and why it fits what we sell. Required to qualify.
    need = models.TextField(blank=True, default="")
    fit = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    # Set once by the import that produced this lead, to make a re-import
    # idempotent (Journey A step 3).
    import_row_key = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        db_table = "crm_lead"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "import_row_key"],
                condition=models.Q(import_row_key__gt=""),
                name="uniq_lead_import_row",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "owner", "status"]),
            models.Index(fields=["workspace", "status", "-created_at"]),
            models.Index(
                fields=["workspace", "-created_at"],
                condition=models.Q(owner__isnull=True, deleted_at__isnull=True),
                name="idx_lead_unassigned",
            ),
        ]

    def __str__(self) -> str:
        return f"Lead for {self.contact_id}"


class Opportunity(WorkspaceScopedModel):
    """One potential sale."""

    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="opportunities")
    lead = models.ForeignKey(
        Lead, null=True, blank=True, on_delete=models.SET_NULL, related_name="opportunities"
    )
    service = models.CharField(max_length=200)
    stage = models.CharField(
        max_length=20, choices=OpportunityStage.choices, default=OpportunityStage.DISCOVERY
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_opportunities",
    )
    # NULL means the value is not yet known. It must never be coerced to zero.
    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        null=True,
        blank=True,
    )
    currency = models.CharField(max_length=3, blank=True, default="")
    expected_close_on = models.DateField(null=True, blank=True)
    # Mirrors the due time of the live next-action task, for cheap queue reads.
    # The task remains the source of truth.
    next_action_at = models.DateTimeField(null=True, blank=True)
    next_action_task = models.ForeignKey(
        "work.Task",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="next_action_for",
    )
    stage_entered_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    # Closing as won requires accepted scope evidence and a recorded commercial
    # decision. It does not mean payment was received (CRM03, CRM11).
    scope_reference = models.CharField(max_length=500, blank=True, default="")
    commercial_reference = models.CharField(max_length=500, blank=True, default="")
    accepted_scope_evidence = models.TextField(blank=True, default="")
    delivery_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="delivery_opportunities",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "crm_opportunity"
        constraints = [
            models.CheckConstraint(
                # An amount without a currency cannot be summed or compared.
                condition=models.Q(amount__isnull=True) | ~models.Q(currency=""),
                name="ck_opportunity_amount_requires_currency",
            ),
            models.CheckConstraint(
                condition=~models.Q(stage=OpportunityStage.LOST) | ~models.Q(lost_reason=""),
                name="ck_opportunity_lost_requires_reason",
            ),
            models.CheckConstraint(
                condition=~models.Q(stage=OpportunityStage.WON)
                | (~models.Q(accepted_scope_evidence="") & ~models.Q(commercial_reference="")),
                name="ck_opportunity_won_requires_evidence",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "stage", "-created_at"]),
            models.Index(fields=["workspace", "owner", "stage"]),
            models.Index(fields=["workspace", "next_action_at"]),
            models.Index(fields=["workspace", "contact"]),
            models.Index(
                fields=["workspace", "stage_entered_at"],
                condition=models.Q(stage__in=sorted(OPEN_STAGES), deleted_at__isnull=True),
                name="idx_opportunity_open_age",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.service} ({self.stage})"

    @property
    def is_open(self) -> bool:
        return self.stage in OPEN_STAGES


class StageHistory(models.Model):
    """Append-only record of stage movement (CRM03).

    The application role has INSERT and SELECT only on this table; the RLS
    migration withholds UPDATE and DELETE. Pipeline aging and conversion
    reporting rely on it being a faithful record.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="stage_history"
    )
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.CASCADE, related_name="stage_history"
    )
    from_stage = models.CharField(max_length=20, blank=True, default="")
    to_stage = models.CharField(max_length=20)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    changed_at = models.DateTimeField()
    reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "crm_stage_history"
        indexes = [
            models.Index(fields=["workspace", "opportunity", "changed_at"]),
            models.Index(fields=["workspace", "to_stage", "changed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.from_stage or '(new)'} -> {self.to_stage}"


class Activity(WorkspaceScopedModel):
    """A recorded interaction (CRM04).

    ``occurred_at`` is when the interaction happened; ``recorded_at`` is when
    somebody typed it in. Keeping both is what lets a manager see that a week of
    calls was entered in one burst on Friday afternoon -- information a single
    timestamp would destroy.
    """

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="activities")
    opportunity = models.ForeignKey(
        Opportunity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activities",
    )
    lead = models.ForeignKey(
        Lead, null=True, blank=True, on_delete=models.SET_NULL, related_name="activities"
    )
    kind = models.CharField(max_length=20, choices=ActivityKind.choices)
    outcome = models.TextField(blank=True, default="")
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField()
    # Preserved on reassignment: the original author is never rewritten (CRM01).
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="activities"
    )
    evidence_type = models.CharField(
        max_length=24, choices=EvidenceType.choices, default=EvidenceType.SELF_REPORTED
    )
    # Only a real provider reference may accompany PROVIDER_CONFIRMED; the
    # check constraint below enforces that pairing.
    provider_reference = models.CharField(max_length=200, blank=True, default="")
    source_url = models.URLField(blank=True, default="")
    # Corrections append a linked record; the original outcome is not rewritten
    # (Journey D).
    corrects = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="corrections"
    )
    # The next action agreed in this interaction, created from the same form
    # (CRM04: "follow-up creation is available from the activity form").
    follow_up_task = models.ForeignKey(
        "work.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "crm_activity"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(evidence_type=EvidenceType.PROVIDER_CONFIRMED)
                | ~models.Q(provider_reference=""),
                name="ck_activity_confirmed_requires_reference",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "contact", "-occurred_at"]),
            models.Index(fields=["workspace", "opportunity", "-occurred_at"]),
            models.Index(fields=["workspace", "author", "-occurred_at"]),
            models.Index(fields=["workspace", "-occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind} at {self.occurred_at:%Y-%m-%d %H:%M}"


class ImportStatus(models.TextChoices):
    """Lifecycle of one CSV migration batch (CRM02, Journey A)."""

    UPLOADED = "uploaded", "Uploaded, not yet previewed"
    PREVIEWED = "previewed", "Previewed, awaiting confirmation"
    COMMITTED = "committed", "Commit accepted, queued"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class ImportRowState(models.TextChoices):
    PENDING = "pending", "Pending"
    CREATED = "created", "Created"
    DUPLICATE = "duplicate", "Duplicate of an existing record"
    INVALID = "invalid", "Invalid"
    SKIPPED = "skipped", "Skipped by operator choice"


class ImportBatch(WorkspaceScopedModel):
    """One spreadsheet migration (CRM02).

    Commit binds the file hash and the mapping version to an idempotency key,
    so retrying a commit after a timeout resumes the same batch instead of
    importing the rows twice.
    """

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    original_filename = models.CharField(max_length=255)
    # Random storage key under PRIVATE_FILE_ROOT, never the user's filename.
    storage_key = models.CharField(max_length=128)
    byte_size = models.PositiveIntegerField()
    content_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=ImportStatus.choices, default=ImportStatus.UPLOADED
    )
    detected_headers = models.JSONField(default=list, blank=True)
    column_mapping = models.JSONField(default=dict, blank=True)
    mapping_version = models.PositiveIntegerField(default=1)
    # Default country for phone normalisation. Required rather than guessed:
    # inferring it would silently merge unrelated people (CRM02).
    default_country = models.CharField(max_length=2, blank=True, default="")
    default_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    default_source = models.CharField(
        max_length=32, choices=LeadSource.choices, default=LeadSource.IMPORT
    )
    duplicate_policy = models.CharField(
        max_length=16,
        choices=[("skip", "Skip duplicates"), ("update", "Update existing")],
        default="skip",
    )

    total_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)

    committed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Raw uploads expire under the retention policy (section 12.4).
    file_expires_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "crm_import_batch"
        indexes = [
            models.Index(fields=["workspace", "status", "-created_at"]),
            models.Index(fields=["file_expires_at"]),
        ]

    def __str__(self) -> str:
        return f"Import {self.original_filename} ({self.status})"

    @property
    def counts_reconcile(self) -> bool:
        """Totals must account for every input row (CRM02 acceptance)."""
        return self.total_rows == (
            self.created_count + self.duplicate_count + self.invalid_count + self.skipped_count
        )


class ImportRow(models.Model):
    """One spreadsheet row and what became of it.

    ``row_key`` is derived from the batch and the row number, and is unique. It
    is what makes a crashed import resumable: rows already written are detected
    and skipped rather than reprocessed (Journey A step 3).
    """

    id = models.UUIDField(primary_key=True, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="import_rows"
    )
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    row_key = models.CharField(max_length=128)
    raw_values = models.JSONField(default=dict, blank=True)
    state = models.CharField(
        max_length=16, choices=ImportRowState.choices, default=ImportRowState.PENDING
    )
    contact = models.ForeignKey(
        Contact, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    lead = models.ForeignKey(
        Lead, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    errors = models.JSONField(default=list, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "crm_import_row"
        constraints = [
            models.UniqueConstraint(fields=["workspace", "row_key"], name="uniq_import_row_key"),
            models.UniqueConstraint(fields=["batch", "row_number"], name="uniq_import_row_number"),
        ]
        indexes = [models.Index(fields=["batch", "state"])]

    def __str__(self) -> str:
        return f"row {self.row_number}: {self.state}"


class ClientStatus(models.TextChoices):
    """Onboarding lifecycle (SVX-PRD-001 section 5.1)."""

    PENDING_HANDOVER = "pending_handover", "Pending handover"
    ACCEPTED = "accepted", "Handover accepted"
    IN_PROGRESS = "in_progress", "In progress"
    READY = "ready", "Ready"
    COMPLETED = "completed", "Completed"


class Client(WorkspaceScopedModel):
    """A customer, created when a deal is won (CRM07).

    The handover is a two-party act: sales submits it, delivery accepts it or
    returns it with specific missing information. ``status`` therefore starts at
    PENDING_HANDOVER rather than ACCEPTED -- creating the record does not mean
    anybody has agreed to deliver it.
    """

    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="clients")
    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="clients"
    )
    display_name = models.CharField(max_length=200)
    # The deal this client came from. One client per originating deal, enforced
    # below, so a retried conversion cannot create a second one.
    originating_opportunity = models.OneToOneField(
        Opportunity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="converted_client",
    )
    delivery_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="delivery_clients",
    )
    status = models.CharField(
        max_length=20, choices=ClientStatus.choices, default=ClientStatus.PENDING_HANDOVER
    )

    # What sales promised. Carried across at conversion so delivery never has to
    # ask for another spreadsheet (SVX-PRD-001 section 7.2).
    accepted_scope = models.TextField(blank=True, default="")
    exclusions = models.TextField(blank=True, default="")
    commercial_reference = models.CharField(max_length=500, blank=True, default="")
    promised_start_on = models.DateField(null=True, blank=True)
    promised_end_on = models.DateField(null=True, blank=True)

    handover_accepted_at = models.DateTimeField(null=True, blank=True)
    handover_accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    handover_returned_at = models.DateTimeField(null=True, blank=True)
    handover_returned_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "crm_client"
        indexes = [
            models.Index(fields=["workspace", "status"]),
            models.Index(fields=["workspace", "delivery_owner", "status"]),
            models.Index(
                fields=["workspace", "-created_at"],
                condition=models.Q(status="pending_handover", deleted_at__isnull=True),
                name="idx_client_awaiting_handover",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name
