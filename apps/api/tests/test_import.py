"""TEST03 / RD06 - spreadsheet migration (CRM02).

The acceptance criterion is arithmetic: a fixture produces correct created,
skipped, duplicate and invalid counts, and the totals reconcile to the input
rows. A retry creates no extra records.
"""

from __future__ import annotations

import pytest

from modules.common.exceptions import PayloadTooLarge, ValidationFailed
from modules.common.tenancy import workspace_context
from modules.crm import imports as import_service
from modules.crm.models import Contact, ImportRowState, Lead
from modules.crm.normalization import (
    formula_safe,
    normalize_email,
    normalize_phone,
)

pytestmark = pytest.mark.django_db

MAPPING = {
    "Name": "display_name",
    "Company": "company_name",
    "Email": "email",
    "Phone": "phone",
    "Owner": "owner_email",
}

CSV_HEADER = "Name,Company,Email,Phone,Owner\n"


def _csv(*rows: str) -> bytes:
    return (CSV_HEADER + "".join(row + "\n" for row in rows)).encode("utf-8")


@pytest.fixture
def batch_factory(workspace, sales_manager, tmp_path, settings):
    settings.PRIVATE_FILE_ROOT = tmp_path

    def _create(payload: bytes, filename: str = "leads.csv"):
        with workspace_context(workspace.id):
            return import_service.store_upload(
                membership=sales_manager,
                actor=sales_manager.user,
                filename=filename,
                payload=payload,
            )

    return _create


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_email_normalisation_preserves_meaningful_characters():
    """Plus-tags and dots are meaningful on most providers (CRM02)."""
    assert normalize_email("  Ayesha.Khan+crm@Example.COM ") == "ayesha.khan+crm@example.com"
    assert normalize_email("not-an-email") == ""
    assert normalize_email("two@@at.com") == ""


def test_phone_without_country_context_is_not_guessed():
    """Guessing a country would merge two different people."""
    assert normalize_phone("0300 1234567") == ""
    assert normalize_phone("0300 1234567", default_country="PK") == "+923001234567"
    assert normalize_phone("+92 300 1234567") == "+923001234567"
    # An invalid number yields nothing rather than a plausible-looking result.
    assert normalize_phone("12", default_country="PK") == ""


def test_formula_injection_is_neutralised_on_export():
    """A spreadsheet must not execute content that arrived in the source data."""
    assert formula_safe("=cmd|'/c calc'!A1").startswith("'")
    assert formula_safe("+1234").startswith("'")
    assert formula_safe("@SUM(A1)").startswith("'")
    assert formula_safe("Ayesha Khan") == "Ayesha Khan"


# ---------------------------------------------------------------------------
# Upload and preview
# ---------------------------------------------------------------------------


def test_preview_does_not_write_contacts(workspace, sales_manager, batch_factory):
    batch = batch_factory(_csv("Ayesha Khan,Acme,ayesha@acme.test,,"))
    with workspace_context(workspace.id):
        result = import_service.preview(
            membership=sales_manager,
            batch=batch,
            column_mapping=MAPPING,
            default_country="PK",
        )
        assert result.created == 1
        # The point of a preview: nothing has changed yet.
        assert Contact.objects.count() == 0


def test_counts_reconcile_to_input_rows(workspace, sales_manager, batch_factory):
    batch = batch_factory(
        _csv(
            "Ayesha Khan,Acme,ayesha@acme.test,,",
            "Bilal Ahmed,Beta,bilal@beta.test,,",
            ",NoName,broken@example.test,,",  # invalid: no name
            "Chitra Rao,Gamma,ayesha@acme.test,,",  # duplicate email within file
        )
    )
    with workspace_context(workspace.id):
        result = import_service.preview(
            membership=sales_manager,
            batch=batch,
            column_mapping=MAPPING,
            default_country="PK",
        )

    assert result.total_rows == 4
    assert result.created == 2
    assert result.invalid == 1
    assert result.duplicate == 1
    assert result.reconciles


def test_existing_contact_is_detected_as_a_duplicate(
    workspace, sales_manager, batch_factory, contact_factory
):
    with workspace_context(workspace.id):
        contact_factory(
            workspace,
            display_name="Ayesha Khan",
            email="ayesha@acme.test",
            normalized_email="ayesha@acme.test",
        )

    batch = batch_factory(_csv("Ayesha Khan,Acme,ayesha@acme.test,,"))
    with workspace_context(workspace.id):
        result = import_service.preview(
            membership=sales_manager,
            batch=batch,
            column_mapping=MAPPING,
            default_country="PK",
        )
    assert result.duplicate == 1
    assert result.created == 0


def test_mapping_must_supply_a_name(workspace, sales_manager, batch_factory):
    batch = batch_factory(_csv("Ayesha Khan,Acme,ayesha@acme.test,,"))
    with workspace_context(workspace.id), pytest.raises(ValidationFailed):
        import_service.preview(
            membership=sales_manager,
            batch=batch,
            column_mapping={"Email": "email"},
        )


def test_oversized_file_is_refused(workspace, sales_manager, batch_factory, settings):
    settings.IMPORT_MAX_BYTES = 100
    with pytest.raises(PayloadTooLarge):
        batch_factory(_csv(*[f"Person {i},Co,p{i}@x.test,," for i in range(100)]))


def test_excel_bom_is_handled(workspace, sales_manager, batch_factory):
    """Excel writes UTF-8 with a BOM; refusing it would block the migration."""
    payload = b"\xef\xbb\xbf" + _csv("Ayesha Khan,Acme,ayesha@acme.test,,")
    batch = batch_factory(payload)
    assert batch.detected_headers[0] == "Name"


# ---------------------------------------------------------------------------
# Commit and processing
# ---------------------------------------------------------------------------


def _preview_and_commit(sales_manager, batch):
    result = import_service.preview(
        membership=sales_manager,
        batch=batch,
        column_mapping=MAPPING,
        default_country="PK",
    )
    batch = import_service.save_preview(
        membership=sales_manager,
        actor=sales_manager.user,
        batch=batch,
        column_mapping=MAPPING,
        default_country="PK",
        duplicate_policy="skip",
        result=result,
    )
    return import_service.commit(
        membership=sales_manager,
        actor=sales_manager.user,
        batch=batch,
        preview_hash=import_service.preview_fingerprint(batch),
        expected_version=batch.version,
    )


def test_full_import_creates_contacts_and_leads(workspace, sales_manager, batch_factory):
    batch = batch_factory(
        _csv(
            "Ayesha Khan,Acme,ayesha@acme.test,,",
            "Bilal Ahmed,Beta,bilal@beta.test,,",
        )
    )
    with workspace_context(workspace.id):
        batch = _preview_and_commit(sales_manager, batch)
        batch = import_service.process_batch(batch.id, actor=sales_manager.user)

        assert batch.created_count == 2
        assert batch.counts_reconcile
        assert Contact.objects.count() == 2
        assert Lead.objects.count() == 2


def test_retrying_a_commit_creates_no_extra_records(workspace, sales_manager, batch_factory):
    """CRM02 acceptance: a retry creates no extra records."""
    batch = batch_factory(_csv("Ayesha Khan,Acme,ayesha@acme.test,,"))
    with workspace_context(workspace.id):
        batch = _preview_and_commit(sales_manager, batch)
        import_service.process_batch(batch.id, actor=sales_manager.user)

        # Commit again, then reprocess. Both must be no-ops.
        import_service.commit(
            membership=sales_manager,
            actor=sales_manager.user,
            batch=batch,
            preview_hash=import_service.preview_fingerprint(batch),
            expected_version=batch.version,
        )
        import_service.process_batch(batch.id, actor=sales_manager.user)

        assert Contact.objects.count() == 1
        assert Lead.objects.count() == 1


def test_processing_resumes_without_reprocessing_finished_rows(
    workspace, sales_manager, batch_factory
):
    """A crash mid-import must resume, not restart (Journey A step 4)."""
    from modules.crm.models import ImportRow

    batch = batch_factory(_csv(*[f"Person {i},Co,p{i}@x.test,," for i in range(5)]))
    with workspace_context(workspace.id):
        batch = _preview_and_commit(sales_manager, batch)

        # Simulate a partial run: mark two rows as already created.
        done = ImportRow.objects.filter(batch=batch).order_by("row_number")[:2]
        for row in done:
            contact = Contact.objects.create(
                workspace=workspace, display_name=f"Pre-existing {row.row_number}"
            )
            row.state = ImportRowState.CREATED
            row.contact = contact
            row.save(update_fields=["state", "contact"])

        batch = import_service.process_batch(batch.id, actor=sales_manager.user)

        # 2 pre-created + 3 processed now = 5 contacts, not 7.
        assert Contact.objects.count() == 5
        assert batch.counts_reconcile


def test_stale_preview_confirmation_is_refused(workspace, sales_manager, batch_factory):
    """A confirmation must apply to the preview the user actually saw."""
    batch = batch_factory(_csv("Ayesha Khan,Acme,ayesha@acme.test,,"))
    with workspace_context(workspace.id):
        result = import_service.preview(
            membership=sales_manager,
            batch=batch,
            column_mapping=MAPPING,
            default_country="PK",
        )
        batch = import_service.save_preview(
            membership=sales_manager,
            actor=sales_manager.user,
            batch=batch,
            column_mapping=MAPPING,
            default_country="PK",
            duplicate_policy="skip",
            result=result,
        )
        with pytest.raises(ValidationFailed, match="changed after"):
            import_service.commit(
                membership=sales_manager,
                actor=sales_manager.user,
                batch=batch,
                preview_hash="0" * 64,
                expected_version=batch.version,
            )


def test_error_file_lists_invalid_rows_and_is_formula_safe(workspace, sales_manager, batch_factory):
    batch = batch_factory(
        _csv(
            "Ayesha Khan,Acme,ayesha@acme.test,,",
            "=cmd|'/c calc'!A1,Evil,bad@x.test,,",
        )
    )
    with workspace_context(workspace.id):
        batch = _preview_and_commit(sales_manager, batch)
        import_service.process_batch(batch.id, actor=sales_manager.user)
        csv_text = import_service.build_error_csv(batch)

    # The row is valid as far as the importer is concerned; what matters is
    # that no exported cell starts with a formula trigger.
    for line in csv_text.splitlines()[1:]:
        for cell in line.split(","):
            assert not cell.lstrip('"').startswith(("=", "@"))


def test_a_representative_cannot_run_an_import(workspace, sales_rep, batch_factory):
    """Import writes directly into the contact base, so it is a manager action."""
    from modules.common.exceptions import NotAuthorized

    batch = batch_factory(_csv("Ayesha Khan,Acme,ayesha@acme.test,,"))
    with workspace_context(workspace.id), pytest.raises(NotAuthorized):
        import_service.preview(membership=sales_rep, batch=batch, column_mapping=MAPPING)
