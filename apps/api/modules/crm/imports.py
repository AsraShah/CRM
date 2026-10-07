"""Spreadsheet migration (CRM02, Journey A).

The flow is preview -> map -> commit -> process, and each step exists for a
reason:

* **Preview** never writes contacts. A manager must be able to see what would
  happen, including the duplicate and invalid counts, before anything changes.
* **Commit** binds the file's content hash and the mapping version to an
  idempotency key. Retrying a commit that timed out resumes the same batch
  rather than importing every row a second time.
* **Processing** works in bounded batches of 100 rows, each written atomically
  with its audit and outbox events. A per-row key makes a crash resumable:
  rows already written are detected and skipped, not reprocessed.

Totals must reconcile to the input row count. An import that silently loses
rows is worse than one that fails.
"""

from __future__ import annotations

import csv
import hashlib
import io
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from modules.automation import outbox
from modules.common.audit import AuditAction, record_audit
from modules.common.exceptions import (
    NotAuthorized,
    PayloadTooLarge,
    ValidationFailed,
)
from modules.common.tenancy import current_workspace_id
from modules.crm.models import (
    Contact,
    ImportBatch,
    ImportRow,
    ImportRowState,
    ImportStatus,
    Lead,
    LeadSource,
    LeadStatus,
)
from modules.crm.normalization import (
    find_duplicates,
    formula_safe,
    normalize_email,
    normalize_phone,
)
from modules.identity.models import Membership, User
from modules.identity.policy import has_permission

# The columns a mapping may target. Anything else is ignored rather than
# written somewhere unexpected.
MAPPABLE_FIELDS = (
    "display_name",
    "company_name",
    "email",
    "phone",
    "job_title",
    "source",
    "owner_email",
    "status",
    "next_action",
    "notes",
)

REQUIRED_FIELDS = ("display_name",)


@dataclass(slots=True)
class RowAssessment:
    """What preview concluded about one row."""

    row_number: int
    raw: dict[str, str]
    state: str
    errors: list[str] = field(default_factory=list)
    duplicate_of: Any = None
    normalized: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreviewResult:
    total_rows: int
    created: int
    duplicate: int
    invalid: int
    skipped: int
    rows: list[RowAssessment]

    @property
    def reconciles(self) -> bool:
        return self.total_rows == (self.created + self.duplicate + self.invalid + self.skipped)


def _require(membership: Membership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise NotAuthorized(f"This role may not perform {permission}.")


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def store_upload(
    *, membership: Membership, actor: User, filename: str, payload: bytes
) -> ImportBatch:
    """Validate and store the uploaded file privately.

    Type, size, encoding and row limits are checked before anything is stored.
    The file is written under a random key outside the web root, and expires
    under the retention policy (section 12.4).
    """
    _require(membership, "import.run")

    if len(payload) > settings.IMPORT_MAX_BYTES:
        raise PayloadTooLarge(
            f"The file exceeds the {settings.IMPORT_MAX_BYTES // (1024 * 1024)} MB limit."
        )
    if not filename.lower().endswith((".csv", ".txt")):
        raise ValidationFailed(
            "Only CSV files are accepted.",
            field_errors={"file": ["Expected a .csv file."]},
        )

    text = _decode(payload)
    headers = _read_headers(text)
    row_count = _count_rows(text)
    if row_count > settings.IMPORT_MAX_ROWS:
        raise PayloadTooLarge(
            f"The file holds {row_count} rows; the limit is {settings.IMPORT_MAX_ROWS}."
        )

    storage_key = f"{uuid.uuid4().hex}{secrets.token_hex(8)}.csv"
    target = Path(settings.PRIVATE_FILE_ROOT) / "imports" / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)

    batch = ImportBatch.objects.create(
        workspace_id=current_workspace_id(),
        uploaded_by=actor,
        original_filename=filename[:255],
        storage_key=storage_key,
        byte_size=len(payload),
        content_hash=hashlib.sha256(payload).hexdigest(),
        status=ImportStatus.UPLOADED,
        detected_headers=headers,
        total_rows=row_count,
        file_expires_at=timezone.now() + timedelta(days=settings.IMPORT_FILE_RETENTION_DAYS),
    )
    record_audit(
        action=AuditAction.IMPORT,
        entity_type="crm.ImportBatch",
        entity_id=batch.id,
        actor=actor,
        changes={"filename": batch.original_filename, "rows": row_count},
    )
    return batch


def _decode(payload: bytes) -> str:
    """Decode the upload, tolerating the BOM Excel writes.

    A file exported from Excel on Windows usually arrives as UTF-8 with a BOM or
    as cp1252. Failing on either would block the very migration this feature
    exists for, so both are tried before giving up.
    """
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationFailed(
        "The file could not be read as UTF-8 or Windows-1252 text. "
        "Re-save it as CSV UTF-8 and try again.",
        field_errors={"file": ["Unsupported text encoding."]},
    )


def _read_headers(text: str) -> list[str]:
    reader = csv.reader(io.StringIO(text))
    try:
        return [h.strip() for h in next(reader)]
    except StopIteration:
        raise ValidationFailed(
            "The file is empty.", field_errors={"file": ["No header row found."]}
        ) from None


def _count_rows(text: str) -> int:
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # header
    return sum(1 for row in reader if any(cell.strip() for cell in row))


def _load_rows(batch: ImportBatch) -> list[dict[str, str]]:
    path = Path(settings.PRIVATE_FILE_ROOT) / "imports" / batch.storage_key
    if not path.exists():
        raise ValidationFailed(
            "The uploaded file is no longer available. It may have passed its "
            "retention period; upload it again.",
            code="import_file_expired",
        )
    text = _decode(path.read_bytes())
    reader = csv.DictReader(io.StringIO(text))
    return [
        {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        for row in reader
        if any((v or "").strip() for v in row.values())
    ]


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def preview(
    *,
    membership: Membership,
    batch: ImportBatch,
    column_mapping: dict[str, str],
    default_country: str = "",
    duplicate_policy: str = "skip",
) -> PreviewResult:
    """Assess every row without writing a single contact."""
    _require(membership, "import.run")

    unknown = set(column_mapping.values()) - set(MAPPABLE_FIELDS)
    if unknown:
        raise ValidationFailed(
            f"Unknown target fields in the mapping: {sorted(unknown)}.",
            field_errors={"column_mapping": [f"Unknown: {sorted(unknown)}"]},
        )
    mapped_targets = set(column_mapping.values())
    missing = [f for f in REQUIRED_FIELDS if f not in mapped_targets]
    if missing:
        raise ValidationFailed(
            f"The mapping must supply: {missing}.",
            field_errors={"column_mapping": [f"Missing required: {missing}"]},
        )

    rows = _load_rows(batch)
    workspace_id = current_workspace_id()
    owner_lookup = _owner_lookup(workspace_id)

    assessments: list[RowAssessment] = []
    # Values already claimed by an earlier row of this same file. Without this,
    # a spreadsheet containing the same address twice would pass preview and
    # then fail on the unique index mid-import.
    seen_emails: set[str] = set()
    seen_phones: set[str] = set()

    for index, raw in enumerate(rows, start=1):
        assessment = _assess_row(
            raw=raw,
            row_number=index,
            column_mapping=column_mapping,
            default_country=default_country,
            workspace_id=workspace_id,
            owner_lookup=owner_lookup,
            duplicate_policy=duplicate_policy,
            seen_emails=seen_emails,
            seen_phones=seen_phones,
        )
        assessments.append(assessment)

    counts = dict.fromkeys(("created", "duplicate", "invalid", "skipped"), 0)
    for assessment in assessments:
        key = {
            ImportRowState.CREATED: "created",
            ImportRowState.DUPLICATE: "duplicate",
            ImportRowState.INVALID: "invalid",
            ImportRowState.SKIPPED: "skipped",
        }[assessment.state]
        counts[key] += 1

    return PreviewResult(
        total_rows=len(assessments),
        created=counts["created"],
        duplicate=counts["duplicate"],
        invalid=counts["invalid"],
        skipped=counts["skipped"],
        rows=assessments,
    )


def _assess_row(
    *,
    raw: dict[str, str],
    row_number: int,
    column_mapping: dict[str, str],
    default_country: str,
    workspace_id,
    owner_lookup: dict[str, User],
    duplicate_policy: str,
    seen_emails: set[str],
    seen_phones: set[str],
) -> RowAssessment:
    values = {target: raw.get(source, "") for source, target in column_mapping.items()}
    errors: list[str] = []

    display_name = values.get("display_name", "").strip()
    if not display_name:
        errors.append("A name is required.")

    raw_email = values.get("email", "").strip()
    normalized_email = normalize_email(raw_email)
    if raw_email and not normalized_email:
        errors.append(f"{raw_email!r} is not a usable email address.")

    raw_phone = values.get("phone", "").strip()
    phone_e164 = normalize_phone(raw_phone, default_country=default_country)
    if raw_phone and not phone_e164:
        # Not an error: an unparseable number is kept as written and simply not
        # used for matching. Missing optional data must not force staff to
        # invent values (CRM02).
        pass

    owner_email = normalize_email(values.get("owner_email", ""))
    owner = owner_lookup.get(owner_email) if owner_email else None
    if owner_email and owner is None:
        errors.append(f"No active member matches the owner {owner_email!r}.")

    source = values.get("source", "").strip().lower()
    if source and source not in LeadSource.values:
        source = LeadSource.OTHER

    if errors:
        return RowAssessment(row_number, raw, ImportRowState.INVALID, errors)

    if normalized_email and normalized_email in seen_emails:
        return RowAssessment(
            row_number,
            raw,
            ImportRowState.DUPLICATE,
            ["This address appears earlier in the same file."],
        )
    if phone_e164 and phone_e164 in seen_phones:
        return RowAssessment(
            row_number,
            raw,
            ImportRowState.DUPLICATE,
            ["This phone number appears earlier in the same file."],
        )

    matches = find_duplicates(
        workspace_id=workspace_id,
        normalized_email=normalized_email,
        phone_e164=phone_e164,
        display_name=display_name,
    )
    definitive = [m for m in matches if m.is_definitive]
    if definitive and duplicate_policy == "skip":
        return RowAssessment(
            row_number,
            raw,
            ImportRowState.DUPLICATE,
            [definitive[0].reason],
            duplicate_of=definitive[0].contact_id,
        )

    if normalized_email:
        seen_emails.add(normalized_email)
    if phone_e164:
        seen_phones.add(phone_e164)

    return RowAssessment(
        row_number,
        raw,
        ImportRowState.CREATED,
        # Name-similarity matches are surfaced as a note, never auto-merged.
        [m.reason for m in matches if not m.is_definitive],
        normalized={
            "display_name": display_name,
            "company_name": values.get("company_name", "").strip(),
            "email": raw_email,
            "normalized_email": normalized_email,
            "phone": raw_phone,
            "phone_e164": phone_e164,
            "job_title": values.get("job_title", "").strip(),
            "source": source or LeadSource.IMPORT,
            "owner_id": str(owner.id) if owner else None,
            "notes": values.get("notes", "").strip(),
        },
    )


def _owner_lookup(workspace_id) -> dict[str, User]:
    from modules.identity.models import MembershipStatus

    return {
        m.user.email.lower(): m.user
        for m in Membership.objects.select_related("user").filter(
            workspace_id=workspace_id, status=MembershipStatus.ACTIVE
        )
    }


@transaction.atomic
def save_preview(
    *,
    membership: Membership,
    actor: User,
    batch: ImportBatch,
    column_mapping: dict[str, str],
    default_country: str,
    duplicate_policy: str,
    result: PreviewResult,
) -> ImportBatch:
    """Persist the mapping and per-row assessment so commit can act on it."""
    _require(membership, "import.run")

    ImportRow.objects.filter(batch=batch).delete()
    ImportRow.objects.bulk_create(
        [
            ImportRow(
                id=uuid.uuid4(),
                workspace_id=batch.workspace_id,
                batch=batch,
                row_number=assessment.row_number,
                row_key=_row_key(batch, assessment.row_number),
                raw_values=assessment.raw,
                state=ImportRowState.PENDING
                if assessment.state == ImportRowState.CREATED
                else assessment.state,
                errors=assessment.errors,
            )
            for assessment in result.rows
        ],
        batch_size=500,
    )

    batch.column_mapping = column_mapping
    batch.mapping_version += 1
    batch.default_country = default_country.upper()[:2]
    batch.duplicate_policy = duplicate_policy
    batch.status = ImportStatus.PREVIEWED
    batch.total_rows = result.total_rows
    batch.duplicate_count = result.duplicate
    batch.invalid_count = result.invalid
    batch.skipped_count = result.skipped
    batch.created_count = 0
    batch.version += 1
    batch.save()
    return batch


def _row_key(batch: ImportBatch, row_number: int) -> str:
    """Stable per-row identity for this batch and mapping.

    Includes the content hash so that re-uploading a *different* file cannot
    collide with an earlier batch's keys.
    """
    return f"{batch.id}:{batch.content_hash[:16]}:{row_number}"


# ---------------------------------------------------------------------------
# Commit and processing
# ---------------------------------------------------------------------------


@transaction.atomic
def commit(
    *,
    membership: Membership,
    actor: User,
    batch: ImportBatch,
    preview_hash: str,
    expected_version: int,
) -> ImportBatch:
    """Accept the previewed mapping and queue processing.

    ``preview_hash`` is the client's assertion about which preview it is
    confirming. If the mapping changed since, the confirmation is stale and is
    rejected rather than applied to a different plan.
    """
    _require(membership, "import.run")

    if batch.status in (ImportStatus.COMMITTED, ImportStatus.PROCESSING):
        # A retried commit is not an error; it returns the batch already queued.
        return batch
    if batch.status == ImportStatus.COMPLETED:
        return batch
    if batch.status != ImportStatus.PREVIEWED:
        raise ValidationFailed("This batch has not been previewed yet.")
    if batch.version != expected_version:
        from modules.common.exceptions import VersionConflict

        raise VersionConflict(expected=expected_version, current=batch.version)

    expected_hash = preview_fingerprint(batch)
    if preview_hash != expected_hash:
        raise ValidationFailed(
            "The mapping changed after this preview was generated. "
            "Review the preview again before confirming.",
            code="stale_preview",
        )

    batch.status = ImportStatus.COMMITTED
    batch.committed_at = timezone.now()
    batch.version += 1
    batch.save(update_fields=["status", "committed_at", "version", "updated_at"])

    record_audit(
        action=AuditAction.IMPORT,
        entity_type="crm.ImportBatch",
        entity_id=batch.id,
        actor=actor,
        changes={"status": ImportStatus.COMMITTED, "rows": batch.total_rows},
    )
    outbox.emit(
        event_type="import.committed",
        aggregate_type="import_batch",
        aggregate_id=batch.id,
        aggregate_version=batch.version,
        payload={"actor_id": str(actor.id)},
    )
    return batch


def preview_fingerprint(batch: ImportBatch) -> str:
    """Identify one exact preview: this file, this mapping, this policy."""
    canonical = "|".join(
        [
            batch.content_hash,
            str(batch.mapping_version),
            repr(sorted(batch.column_mapping.items())),
            batch.default_country,
            batch.duplicate_policy,
        ]
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def process_batch(batch_id: uuid.UUID, *, actor: User | None = None) -> ImportBatch:
    """Process a committed batch in bounded chunks.

    Each chunk commits on its own, so a crash loses at most one chunk's work and
    the remaining rows resume from where they stopped. Rows already in a
    terminal state are never reprocessed.
    """
    batch = ImportBatch.objects.get(pk=batch_id, workspace_id=current_workspace_id())
    if batch.status == ImportStatus.COMPLETED:
        return batch
    if batch.status not in (ImportStatus.COMMITTED, ImportStatus.PROCESSING):
        raise ValidationFailed("This batch is not ready for processing.")

    ImportBatch.objects.filter(pk=batch.pk).update(status=ImportStatus.PROCESSING)

    rows_by_number = {a.row_number: a for a in _reassess(batch)}
    chunk = settings.IMPORT_BATCH_SIZE

    while True:
        pending_ids = list(
            ImportRow.objects.filter(batch=batch, state=ImportRowState.PENDING)
            .order_by("row_number")
            .values_list("id", flat=True)[:chunk]
        )
        if not pending_ids:
            break
        _process_chunk(batch, pending_ids, rows_by_number, actor)

    return _finalise_batch(batch, actor)


def _reassess(batch: ImportBatch) -> list[RowAssessment]:
    """Re-run the assessment at processing time.

    The contact base may have changed between preview and commit, so duplicate
    decisions are made against current data rather than replaying a stale
    verdict.
    """
    rows = _load_rows(batch)
    workspace_id = batch.workspace_id
    owner_lookup = _owner_lookup(workspace_id)
    seen_emails: set[str] = set()
    seen_phones: set[str] = set()
    return [
        _assess_row(
            raw=raw,
            row_number=index,
            column_mapping=batch.column_mapping,
            default_country=batch.default_country,
            workspace_id=workspace_id,
            owner_lookup=owner_lookup,
            duplicate_policy=batch.duplicate_policy,
            seen_emails=seen_emails,
            seen_phones=seen_phones,
        )
        for index, raw in enumerate(rows, start=1)
    ]


@transaction.atomic
def _process_chunk(
    batch: ImportBatch,
    row_ids: list[uuid.UUID],
    assessments: dict[int, RowAssessment],
    actor: User | None,
) -> None:
    """Write one chunk of rows, atomically with their audit and outbox events."""
    rows = list(
        ImportRow.objects.select_for_update()
        .filter(id__in=row_ids, state=ImportRowState.PENDING)
        .order_by("row_number")
    )

    for row in rows:
        assessment = assessments.get(row.row_number)
        if assessment is None or assessment.state != ImportRowState.CREATED:
            row.state = assessment.state if assessment else ImportRowState.INVALID
            row.errors = assessment.errors if assessment else ["Row is no longer present."]
            row.processed_at = timezone.now()
            row.save(update_fields=["state", "errors", "processed_at"])
            continue

        try:
            contact, lead = _create_from_row(batch, row, assessment, actor)
        except IntegrityError as exc:
            # A unique index rejected the write: another row or a concurrent
            # import already claimed this address. Record it as a duplicate
            # rather than failing the whole batch.
            row.state = ImportRowState.DUPLICATE
            row.errors = [f"Rejected by a uniqueness rule: {type(exc).__name__}."]
            row.processed_at = timezone.now()
            row.save(update_fields=["state", "errors", "processed_at"])
            continue

        row.state = ImportRowState.CREATED
        row.contact = contact
        row.lead = lead
        row.processed_at = timezone.now()
        row.save(update_fields=["state", "contact", "lead", "processed_at"])


def _create_from_row(
    batch: ImportBatch, row: ImportRow, assessment: RowAssessment, actor: User | None
) -> tuple[Contact, Lead]:
    from modules.crm.models import Company

    data = assessment.normalized
    company = None
    if data.get("company_name"):
        company, _ = Company.objects.get_or_create(
            workspace_id=batch.workspace_id,
            normalized_name=data["company_name"].strip().lower(),
            defaults={"name": data["company_name"].strip()},
        )

    contact = Contact.objects.create(
        workspace_id=batch.workspace_id,
        display_name=data["display_name"],
        company=company,
        email=data["email"],
        normalized_email=data["normalized_email"],
        phone=data["phone"],
        phone_e164=data["phone_e164"],
        job_title=data["job_title"],
        source=data["source"],
        source_reference=f"import:{batch.id}",
        # Keep the row exactly as supplied, so a normalisation defect can be
        # diagnosed without the original spreadsheet.
        raw_import_values=row.raw_values,
    )

    lead = Lead.objects.create(
        workspace_id=batch.workspace_id,
        contact=contact,
        owner_id=data.get("owner_id"),
        source=data["source"],
        source_reference=f"import:{batch.id}",
        status=LeadStatus.ASSIGNED if data.get("owner_id") else LeadStatus.NEW,
        notes=data.get("notes", ""),
        # The per-row key is what makes a restart safe.
        import_row_key=row.row_key,
    )

    outbox.emit(
        event_type="lead.created",
        aggregate_type="lead",
        aggregate_id=lead.id,
        aggregate_version=lead.version,
        payload={"owner_id": data.get("owner_id"), "import_batch": str(batch.id)},
    )
    return contact, lead


@transaction.atomic
def _finalise_batch(batch: ImportBatch, actor: User | None) -> ImportBatch:
    """Reconcile the counts and close the batch.

    If the totals do not add up to the input row count, the batch is marked
    failed. A silently lossy import is the outcome CRM02 exists to prevent.
    """
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    counts = dict.fromkeys(ImportRowState.values, 0)
    tallies = ImportRow.objects.filter(batch=batch).values("state").annotate(total=Count("id"))
    for entry in tallies:
        counts[entry["state"]] = entry["total"]

    batch.created_count = counts.get(ImportRowState.CREATED, 0)
    batch.duplicate_count = counts.get(ImportRowState.DUPLICATE, 0)
    batch.invalid_count = counts.get(ImportRowState.INVALID, 0)
    batch.skipped_count = counts.get(ImportRowState.SKIPPED, 0)
    batch.completed_at = timezone.now()

    if not batch.counts_reconcile:
        accounted = (
            batch.created_count + batch.duplicate_count + batch.invalid_count + batch.skipped_count
        )
        batch.status = ImportStatus.FAILED
        batch.failure_reason = (
            f"Counts do not reconcile: {batch.total_rows} input rows against "
            f"{accounted} accounted rows."
        )
    else:
        batch.status = ImportStatus.COMPLETED

    batch.version += 1
    batch.save()

    record_audit(
        action=AuditAction.IMPORT,
        entity_type="crm.ImportBatch",
        entity_id=batch.id,
        actor=actor,
        changes={
            "status": batch.status,
            "created": batch.created_count,
            "duplicate": batch.duplicate_count,
            "invalid": batch.invalid_count,
            "skipped": batch.skipped_count,
        },
    )
    return batch


# ---------------------------------------------------------------------------
# Error file
# ---------------------------------------------------------------------------


def build_error_csv(batch: ImportBatch) -> str:
    """Produce an actionable, formula-safe error file (CRM02).

    Every exported cell is neutralised so that opening the file in Excel cannot
    execute a formula that arrived in the source data.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    headers = batch.detected_headers or []
    writer.writerow(["row_number", "state", "errors", *headers])

    rows = ImportRow.objects.filter(
        batch=batch, state__in=[ImportRowState.INVALID, ImportRowState.DUPLICATE]
    ).order_by("row_number")

    for row in rows.iterator():
        writer.writerow(
            [
                row.row_number,
                row.state,
                formula_safe("; ".join(row.errors)),
                *[formula_safe(row.raw_values.get(h, "")) for h in headers],
            ]
        )
    return buffer.getvalue()
