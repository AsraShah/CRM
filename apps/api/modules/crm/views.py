"""CRM endpoints (SVX-TECH-001 section 6.1).

State changes are explicit action routes rather than an unrestricted status
PATCH, so a stage can only move through the service that validates it.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response

from modules.common.exceptions import NotFound, PayloadTooLarge, ValidationFailed
from modules.common.idempotency import IDEMPOTENCY_HEADER, complete, reserve
from modules.common.permissions import HasWorkspacePermission
from modules.common.viewsets import WorkspaceScopedViewSet
from modules.crm import imports as import_service
from modules.crm import services
from modules.crm.models import Activity, Contact, ImportBatch, Lead, Opportunity, OpportunityStage
from modules.crm.serializers import (
    ActivityCorrectionSerializer,
    ActivityCreateSerializer,
    ActivitySerializer,
    ContactCreateSerializer,
    ContactMergeSerializer,
    ContactSerializer,
    ContactUpdateSerializer,
    ImportBatchSerializer,
    ImportCommitSerializer,
    ImportPreviewRequestSerializer,
    ImportPreviewResponseSerializer,
    LeadAssignSerializer,
    LeadCreateSerializer,
    LeadSerializer,
    LeadStatusChangeSerializer,
    LeadUpdateSerializer,
    OpportunityCreateSerializer,
    OpportunitySerializer,
    OpportunityTransitionSerializer,
    OpportunityUpdateSerializer,
    StageHistorySerializer,
)

PREVIEW_SAMPLE_SIZE = 50


def _get_or_404(model, pk, workspace_id, label: str):
    """Fetch a record inside the workspace, or report it as absent.

    A record in another tenant is reported identically to one that does not
    exist. Distinguishing them would confirm that another workspace holds it.
    """
    if pk is None:
        return None
    instance = model.objects.filter(
        pk=pk, workspace_id=workspace_id, deleted_at__isnull=True
    ).first()
    if instance is None:
        raise NotFound(f"No {label} matches that identifier.")
    return instance


class ContactViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    serializer_class = ContactSerializer
    queryset = Contact.objects.select_related("company").all()
    filterset_fields = ["source", "company"]
    search_fields = ["display_name", "email"]

    def create(self, request):
        serializer = ContactCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contact = services.create_contact(
            membership=request.membership,
            actor=request.user,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(ContactSerializer(contact).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        """PATCH: correct a person's details. Requires the version being edited."""
        serializer = ContactUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        expected_version = changes.pop("expected_version")
        default_country = changes.pop("default_country", "")
        contact = services.update_contact(
            membership=request.membership,
            actor=request.user,
            contact_id=pk,
            expected_version=expected_version,
            changes=changes,
            default_country=default_country,
            request_id=getattr(request, "request_id", ""),
        )
        return Response(ContactSerializer(contact).data)

    @action(detail=True, methods=["get"])
    def duplicates(self, request, pk=None):
        """Contacts that may be the same person. Suggestions for review only."""
        return Response(services.suggest_duplicates(membership=request.membership, contact_id=pk))

    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        """Fold another contact into this one, which survives (CRM02)."""
        serializer = ContactMergeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        survivor = services.merge_contacts(
            membership=request.membership,
            actor=request.user,
            survivor_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(ContactSerializer(survivor).data)


class LeadViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    serializer_class = LeadSerializer
    queryset = Lead.objects.select_related("contact", "owner").all()
    filterset_fields = ["status", "owner", "source", "contact"]

    def get_queryset(self):
        from modules.identity.models import Role

        queryset = super().get_queryset()
        membership = self.request.membership
        # A representative sees their own leads. A manager or owner sees the
        # workspace. This is the list-level half of the rule; retrieval is
        # checked again in the service (CRM01 acceptance).
        if membership.role not in {Role.OWNER, Role.ADMIN, Role.SALES_MANAGER}:
            queryset = queryset.filter(owner_id=membership.user_id)
        return queryset

    def create(self, request):
        serializer = LeadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        contact = _get_or_404(Contact, data["contact"], request.membership.workspace_id, "contact")
        owner = None
        if data.get("owner"):
            from modules.identity.models import User

            owner = User.objects.filter(pk=data["owner"]).first()

        lead = services.create_lead(
            membership=request.membership,
            actor=request.user,
            contact=contact,
            owner=owner,
            source=data.get("source", "unknown"),
            source_reference=data.get("source_reference", ""),
            notes=data.get("notes", ""),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(LeadSerializer(lead).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        """PATCH /leads/{id}: descriptive fields only. Status and owner have actions."""
        serializer = LeadUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        expected_version = changes.pop("expected_version")
        lead = services.update_lead(
            membership=request.membership,
            actor=request.user,
            lead_id=pk,
            expected_version=expected_version,
            changes=changes,
            request_id=getattr(request, "request_id", ""),
        )
        return Response(LeadSerializer(lead).data)

    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, pk=None):
        serializer = LeadStatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lead = services.change_lead_status(
            membership=request.membership,
            actor=request.user,
            lead_id=pk,
            new_status=data["status"],
            expected_version=data["expected_version"],
            nurture_review_at=data.get("nurture_review_at"),
            disqualified_reason=data.get("disqualified_reason", ""),
            need=data.get("need", ""),
            fit=data.get("fit", ""),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(LeadSerializer(lead).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        serializer = LeadAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lead = services.assign_lead(
            membership=request.membership,
            actor=request.user,
            lead_id=pk,
            owner_id=data["owner"],
            expected_version=data["expected_version"],
            reason=data.get("reason", ""),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(LeadSerializer(lead).data)


class OpportunityViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    serializer_class = OpportunitySerializer
    queryset = Opportunity.objects.select_related("contact", "owner", "lead").all()
    filterset_fields = ["stage", "owner", "currency", "contact"]

    def get_queryset(self):
        from django.db.models import Q

        from modules.identity.models import Role

        queryset = super().get_queryset()
        membership = self.request.membership
        if membership.role in {Role.OWNER, Role.ADMIN, Role.SALES_MANAGER}:
            return queryset
        own = Q(owner_id=membership.user_id)
        # "Sales history remains accessible to authorized delivery staff"
        # (CRM07): delivery sees the won deal behind the work it delivers - a
        # delivery manager all of them, an employee those of their own clients.
        # Open deals stay with sales.
        if membership.role == Role.DELIVERY_MANAGER:
            return queryset.filter(own | Q(stage=OpportunityStage.WON))
        if membership.role == Role.DELIVERY_EMPLOYEE:
            return queryset.filter(
                own
                | Q(
                    stage=OpportunityStage.WON,
                    converted_client__delivery_owner_id=membership.user_id,
                )
            )
        return queryset.filter(own)

    def create(self, request):
        serializer = OpportunityCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        workspace_id = request.membership.workspace_id

        contact = _get_or_404(Contact, data["contact"], workspace_id, "contact")
        lead = _get_or_404(Lead, data.get("lead"), workspace_id, "lead")
        owner = None
        if data.get("owner"):
            from modules.identity.models import User

            owner = User.objects.filter(pk=data["owner"]).first()

        opportunity = services.create_opportunity(
            membership=request.membership,
            actor=request.user,
            contact=contact,
            lead=lead,
            service=data["service"],
            owner=owner,
            amount=data.get("amount"),
            currency=data.get("currency", ""),
            expected_close_on=data.get("expected_close_on"),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(OpportunitySerializer(opportunity).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        """PATCH: value, currency, close date and references of an open deal.

        Stage is not editable here; it moves only through transition or convert.
        """
        serializer = OpportunityUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        expected_version = changes.pop("expected_version")
        opportunity = services.update_opportunity(
            membership=request.membership,
            actor=request.user,
            opportunity_id=pk,
            expected_version=expected_version,
            changes=changes,
            request_id=getattr(request, "request_id", ""),
        )
        return Response(OpportunitySerializer(opportunity).data)

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        serializer = OpportunityTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        opportunity = services.transition_opportunity(
            membership=request.membership,
            actor=request.user,
            opportunity_id=pk,
            target_stage=data["target_stage"],
            expected_version=data["expected_version"],
            reason=data.get("reason", ""),
            scope_reference=data.get("scope_reference", ""),
            lost_reason=data.get("lost_reason", ""),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(OpportunitySerializer(opportunity).data)

    @action(detail=True, methods=["get"], url_path="stage-history")
    def stage_history(self, request, pk=None):
        opportunity = self.get_object()
        history = opportunity.stage_history.select_related("actor").order_by("changed_at")
        return Response(StageHistorySerializer(history, many=True).data)

    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        """Close as won and create the client and onboarding project (CRM07).

        Idempotent. A retry after a timeout returns the existing conversion
        rather than creating a second client and project, both through the
        stored idempotency key and through the unique conversion key on the
        project itself.
        """
        from modules.crm import conversion
        from modules.work.delivery_serializers import (
            ClientSerializer,
            ConvertOpportunitySerializer,
            ProjectSerializer,
        )

        serializer = ConvertOpportunitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        key = request.headers.get(IDEMPOTENCY_HEADER, "")
        outcome = None
        if key:
            outcome = reserve(
                actor=request.user,
                route=f"opportunities/{pk}/convert",
                key=key,
                payload=request.data,
            )
            stored = outcome.stored_response
            if stored is not None:
                return Response(stored[1], status=stored[0])

        result = conversion.convert_won_deal(
            membership=request.membership,
            actor=request.user,
            opportunity_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )

        body = {
            "opportunity": OpportunitySerializer(result.opportunity).data,
            "client": ClientSerializer(result.client).data,
            "project": ProjectSerializer(result.project).data,
            # True when this call found an existing conversion. The interface
            # says "already converted" rather than claiming a fresh success.
            "was_existing": result.was_existing,
        }
        response_status = status.HTTP_200_OK if result.was_existing else status.HTTP_201_CREATED
        if outcome is not None:
            complete(outcome.record, status=response_status, body=body)
        return Response(body, status=response_status)


class ActivityViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    serializer_class = ActivitySerializer
    queryset = Activity.objects.select_related("contact", "author", "opportunity").all()
    filterset_fields = ["kind", "contact", "opportunity", "evidence_type"]

    def create(self, request):
        serializer = ActivityCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        workspace_id = request.membership.workspace_id

        activity = services.record_activity(
            membership=request.membership,
            actor=request.user,
            contact=_get_or_404(Contact, data["contact"], workspace_id, "contact"),
            opportunity=_get_or_404(
                Opportunity, data.get("opportunity"), workspace_id, "opportunity"
            ),
            lead=_get_or_404(Lead, data.get("lead"), workspace_id, "lead"),
            kind=data["kind"],
            occurred_at=data["occurred_at"],
            outcome=data.get("outcome", ""),
            source_url=data.get("source_url", ""),
            follow_up_title=data.get("follow_up_title", ""),
            follow_up_due_at=data.get("follow_up_due_at"),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(ActivitySerializer(activity).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def correct(self, request, pk=None):
        serializer = ActivityCorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        correction = services.correct_activity(
            membership=request.membership,
            actor=request.user,
            activity_id=pk,
            outcome=serializer.validated_data["outcome"],
            reason=serializer.validated_data["reason"],
            request_id=getattr(request, "request_id", ""),
        )
        return Response(ActivitySerializer(correction).data, status=status.HTTP_201_CREATED)


class ImportBatchViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    """CSV migration (CRM02, Journey A)."""

    serializer_class = ImportBatchSerializer
    queryset = ImportBatch.objects.all()
    permission_classes = [HasWorkspacePermission]
    required_permission = "import.run"
    throttle_scope = "imports"
    # Only an upload starts new work; previewing and polling a batch must not
    # spend the allowance.
    throttle_scope_actions = {"create"}

    def create(self, request):
        """Upload a CSV. Nothing is written to the contact base yet."""
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationFailed(
                "A CSV file is required.",
                field_errors={"file": ["This field is required."]},
            )
        # Checked before read(): the service checks too, but only after the
        # whole file is in memory, which an oversized upload must never reach.
        if upload.size > settings.IMPORT_MAX_BYTES:
            raise PayloadTooLarge(
                f"The file exceeds the {settings.IMPORT_MAX_BYTES // (1024 * 1024)} MB limit."
            )
        batch = import_service.store_upload(
            membership=request.membership,
            actor=request.user,
            filename=upload.name,
            payload=upload.read(),
        )
        return Response(ImportBatchSerializer(batch).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None):
        """Assess every row and report the counts, without writing contacts."""
        batch = self.get_object()
        serializer = ImportPreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = import_service.preview(
            membership=request.membership,
            batch=batch,
            column_mapping=data["column_mapping"],
            default_country=data.get("default_country", ""),
            duplicate_policy=data.get("duplicate_policy", "skip"),
        )
        batch = import_service.save_preview(
            membership=request.membership,
            actor=request.user,
            batch=batch,
            column_mapping=data["column_mapping"],
            default_country=data.get("default_country", ""),
            duplicate_policy=data.get("duplicate_policy", "skip"),
            result=result,
        )

        payload = {
            "batch": batch,
            "total_rows": result.total_rows,
            "created": result.created,
            "duplicate": result.duplicate,
            "invalid": result.invalid,
            "skipped": result.skipped,
            "reconciles": result.reconciles,
            "sample_rows": [
                {
                    "row_number": row.row_number,
                    "state": row.state,
                    "errors": row.errors,
                    "raw": row.raw,
                }
                for row in result.rows[:PREVIEW_SAMPLE_SIZE]
            ],
            # The client echoes this back on commit. A changed mapping produces
            # a different hash, so a stale confirmation is refused.
            "preview_hash": import_service.preview_fingerprint(batch),
        }
        return Response(ImportPreviewResponseSerializer(payload).data)

    @action(detail=True, methods=["post"])
    def commit(self, request, pk=None):
        """Confirm the preview and queue processing.

        Idempotent: a retry after a timeout returns the original response
        instead of starting a second import (section 6.2).
        """
        batch = self.get_object()
        serializer = ImportCommitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        key = request.headers.get(IDEMPOTENCY_HEADER, "")
        outcome = None
        if key:
            outcome = reserve(
                actor=request.user,
                route=f"imports/{batch.id}/commit",
                key=key,
                payload=request.data,
            )
            stored = outcome.stored_response
            if stored is not None:
                return Response(stored[1], status=stored[0])

        batch = import_service.commit(
            membership=request.membership,
            actor=request.user,
            batch=batch,
            preview_hash=serializer.validated_data["preview_hash"],
            expected_version=serializer.validated_data["expected_version"],
        )
        body = ImportBatchSerializer(batch).data
        if outcome is not None:
            complete(outcome.record, status=status.HTTP_202_ACCEPTED, body=body)
        return Response(body, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"], url_path="errors.csv")
    def error_file(self, request, pk=None):
        """Download the invalid and duplicate rows.

        Every cell is prefixed if it would otherwise be evaluated as a formula,
        so opening the file cannot execute content that arrived in the source
        spreadsheet (CRM02).
        """
        batch = self.get_object()
        csv_text = import_service.build_error_csv(batch)
        response = HttpResponse(csv_text, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="import-{batch.id}-errors.csv"'
        return response
