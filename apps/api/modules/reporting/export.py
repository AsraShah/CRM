"""Workspace export (CRM13).

Authorised managers export core business records **with relationships and
stable identifiers**. Stable identifiers are the point: an export whose rows
cannot be reconnected to each other is a pile of spreadsheets, not a portable
record of the business.

Three properties this module guarantees:

* **Authorised.** Export is owner-only and MFA-gated. It removes data from the
  access-controlled system, so it is the most consequential read in the product.
* **Audited.** Every export writes an audit event naming the actor and what was
  taken. An export nobody can account for later is a gap in the record.
* **Formula-safe.** Every CSV cell that a spreadsheet would evaluate is
  neutralised, so opening the export cannot execute content that arrived from a
  lead form or an imported file.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Any

from django.utils import timezone

from modules.common.audit import AuditAction, record_audit
from modules.common.exceptions import NotAuthorized
from modules.common.tenancy import current_workspace_id
from modules.crm.models import (
    Activity,
    Client,
    Company,
    Contact,
    Lead,
    Opportunity,
    StageHistory,
)
from modules.crm.normalization import formula_safe
from modules.identity.models import Membership, User
from modules.identity.policy import has_permission
from modules.identity.resolution import assert_membership_still_active
from modules.reporting.models import CashReceipt
from modules.support.models import Ticket, TicketComment
from modules.work.models import Milestone, Project, Task

#: Each table's exported columns. UUIDs throughout, so relationships survive.
EXPORT_TABLES: dict[str, tuple[type, tuple[str, ...]]] = {
    "companies": (Company, ("id", "name", "website", "created_at")),
    "contacts": (
        Contact,
        (
            "id",
            "display_name",
            "company_id",
            "email",
            "phone",
            "phone_e164",
            "job_title",
            "source",
            "created_at",
        ),
    ),
    "leads": (
        Lead,
        (
            "id",
            "contact_id",
            "owner_id",
            "source",
            "status",
            "qualified_at",
            "disqualified_reason",
            "created_at",
        ),
    ),
    "opportunities": (
        Opportunity,
        (
            "id",
            "contact_id",
            "lead_id",
            "service",
            "stage",
            "owner_id",
            "amount",
            "currency",
            "expected_close_on",
            "scope_reference",
            "commercial_reference",
            "closed_at",
            "lost_reason",
            "created_at",
        ),
    ),
    "stage_history": (
        StageHistory,
        ("id", "opportunity_id", "from_stage", "to_stage", "actor_id", "changed_at", "reason"),
    ),
    "activities": (
        Activity,
        (
            "id",
            "contact_id",
            "opportunity_id",
            "kind",
            "outcome",
            "occurred_at",
            "recorded_at",
            "author_id",
            # Exported so the distinction survives outside the system. Without
            # it, every activity in the export looks equally verified.
            "evidence_type",
            "provider_reference",
            "corrects_id",
        ),
    ),
    "clients": (
        Client,
        (
            "id",
            "contact_id",
            "display_name",
            "originating_opportunity_id",
            "delivery_owner_id",
            "status",
            "accepted_scope",
            "commercial_reference",
            "handover_accepted_at",
            "created_at",
        ),
    ),
    "projects": (
        Project,
        (
            "id",
            "client_id",
            "originating_opportunity_id",
            "name",
            "status",
            "owner_id",
            "starts_on",
            "due_on",
            "completed_at",
        ),
    ),
    "milestones": (
        Milestone,
        (
            "id",
            "project_id",
            "name",
            "sequence",
            "status",
            "owner_id",
            "due_on",
            "submitted_at",
            "submitted_by_id",
            "accepted_at",
            "accepted_by_id",
            "blocked_reason",
        ),
    ),
    "tasks": (
        Task,
        (
            "id",
            "title",
            "kind",
            "status",
            "owner_id",
            "origin",
            "due_at",
            "original_due_at",
            "reschedule_count",
            "contact_id",
            "opportunity_id",
            "completed_at",
            "outcome",
        ),
    ),
    "tickets": (
        Ticket,
        (
            "id",
            "title",
            "origin",
            "priority",
            "state",
            "client_id",
            "project_id",
            "owner_id",
            "resolution_note",
            "closure_test_result",
            "resolved_at",
            "reopen_count",
            "created_at",
        ),
    ),
    "ticket_comments": (
        TicketComment,
        ("id", "ticket_id", "author_id", "body", "visibility", "created_at"),
    ),
    "cash_receipts": (
        CashReceipt,
        (
            "id",
            "client_id",
            "opportunity_id",
            "amount",
            "currency",
            "received_at",
            "evidence_reference",
            "adjusts_id",
            "recorded_by_id",
        ),
    ),
}


def build_export(*, membership: Membership, actor: User, request_id: str = "") -> bytes:
    """Produce a ZIP of CSVs plus a manifest.

    Returns bytes rather than streaming: at pilot scale the whole workspace is
    a few megabytes, and a single atomic artefact is far easier to verify than a
    stream that may have truncated.
    """
    if not has_permission(membership, "workspace.export"):
        raise NotAuthorized(
            "Exporting the workspace is restricted to the owner, with a second factor enrolled."
        )
    # Re-read the membership under lock. The gap between the permission check
    # and the read matters here more than anywhere else in the product.
    assert_membership_still_active(membership)

    workspace_id = current_workspace_id()
    generated_at = timezone.now()
    manifest: dict[str, Any] = {
        "workspace_id": str(workspace_id),
        "generated_at": generated_at.isoformat(),
        "generated_by": actor.email,
        "format": "CSV, UTF-8, RFC 4180",
        "identifier_scheme": "UUID, stable across exports",
        "tables": {},
        "notes": [
            "Identifiers are stable UUIDs; foreign key columns reference them "
            "directly, so relationships can be rebuilt.",
            "Cells beginning with a formula character are prefixed with an "
            "apostrophe so that spreadsheet software renders them as text.",
            "Soft-deleted records are excluded. Soft deletion is not erasure.",
            "Audit events are not included: they are retained under a separate "
            "procedure and a separate maintenance identity.",
        ],
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for table_name, (model, columns) in EXPORT_TABLES.items():
            rows_written, csv_text = _export_table(model, columns, workspace_id)
            archive.writestr(f"{table_name}.csv", csv_text)
            manifest["tables"][table_name] = {
                "rows": rows_written,
                "columns": list(columns),
            }

        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        archive.writestr("README.txt", _readme(manifest))

    record_audit(
        action=AuditAction.EXPORT,
        entity_type="identity.Workspace",
        entity_id=workspace_id,
        actor=actor,
        changes={"tables": {name: info["rows"] for name, info in manifest["tables"].items()}},
        reason="Workspace export",
        request_id=request_id,
    )
    return buffer.getvalue()


def _export_table(model, columns: tuple[str, ...], workspace_id) -> tuple[int, str]:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)

    queryset = model.objects.filter(workspace_id=workspace_id)
    if hasattr(model, "deleted_at"):
        queryset = queryset.filter(deleted_at__isnull=True)

    count = 0
    for values in queryset.values_list(*columns).iterator(chunk_size=500):
        writer.writerow([formula_safe(_render(v)) for v in values])
        count += 1
    return count, output.getvalue()


def _render(value: Any) -> str:
    if value is None:
        # Distinct from an empty string, which is a real value.
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _readme(manifest: dict[str, Any]) -> str:
    lines = [
        "ScaleVexo CRM workspace export",
        "=" * 30,
        "",
        f"Generated: {manifest['generated_at']}",
        f"By: {manifest['generated_by']}",
        "",
        "Contents",
        "--------",
    ]
    for name, info in manifest["tables"].items():
        lines.append(f"  {name}.csv — {info['rows']} rows")
    lines += [
        "",
        "Rebuilding relationships",
        "------------------------",
        "Every row has a UUID `id`. Columns ending in `_id` reference the `id`",
        "of the named table. Import the files in the order listed above and the",
        "foreign keys will resolve.",
        "",
        "Important",
        "---------",
        "Cells starting with = + - or @ are prefixed with an apostrophe. That is",
        "deliberate: it stops spreadsheet software executing content that came",
        "from an imported file or a web form. Strip the apostrophe only if you",
        "know the source is trustworthy.",
        "",
        "This export contains commercial and personal data. Handle it under the",
        "same controls as the system it came from.",
    ]
    return "\n".join(lines)
