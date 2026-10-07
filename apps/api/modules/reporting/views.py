"""Reporting, receipts, exceptions and export endpoints (CRM10, CRM11, CRM13)."""

from __future__ import annotations

from datetime import date

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from modules.common import limits
from modules.common.audit import AuditAction, record_audit
from modules.common.exceptions import NotAuthorized, ValidationFailed
from modules.common.permissions import HasWorkspacePermission
from modules.common.viewsets import WorkspaceAPIView, WorkspaceScopedViewSet
from modules.crm.models import Client, Opportunity
from modules.identity.policy import has_permission
from modules.reporting import exceptions_service, export, metrics
from modules.reporting.models import CashReceipt, WorkException

# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


class CashReceiptSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.display_name", read_only=True)

    class Meta:
        model = CashReceipt
        fields = [
            "id",
            "client",
            "client_name",
            "opportunity",
            "amount",
            "currency",
            "received_at",
            "evidence_reference",
            "note",
            "recorded_by",
            "adjusts",
            "adjustment_reason",
            "created_at",
        ]
        read_only_fields = ["id", "recorded_by", "created_at"]


class CashReceiptCreateSerializer(serializers.Serializer):
    client = serializers.UUIDField()
    opportunity = serializers.UUIDField(required=False, allow_null=True)
    # Signed: a correction may reduce an earlier receipt. The view refuses a
    # non-positive amount on anything that is not a correction.
    amount = limits.money(minimum=None)
    currency = limits.currency()
    received_at = serializers.DateField()
    # Mandatory. An unevidenced figure is a claim, not a record.
    evidence_reference = serializers.CharField(max_length=200)
    note = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    # Present only when correcting an earlier receipt; the original stays.
    adjusts = serializers.UUIDField(required=False, allow_null=True)
    adjustment_reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class WorkExceptionSerializer(serializers.ModelSerializer):
    subject_email = serializers.EmailField(source="subject.email", read_only=True, default=None)

    class Meta:
        model = WorkException
        fields = [
            "id",
            "kind",
            "state",
            "subject",
            "subject_email",
            "entity_type",
            "entity_id",
            "summary",
            # The observable facts. Shown to the employee too, so a dispute is
            # about evidence rather than about a verdict (CRM10).
            "detail",
            "employee_explanation",
            "employee_responded_at",
            "reviewed_by",
            "reviewed_at",
            "review_decision",
            "version",
            "created_at",
        ]
        read_only_fields = fields


class ExceptionExplainSerializer(serializers.Serializer):
    explanation = serializers.CharField(max_length=5000)
    dispute = serializers.BooleanField(default=False)


class ExceptionReviewSerializer(serializers.Serializer):
    decision = serializers.CharField(max_length=5000)
    dismiss = serializers.BooleanField(default=False)
    expected_version = serializers.IntegerField(min_value=1, required=False)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def _period(request) -> tuple[date, date]:
    today = timezone.now().date()
    start = request.query_params.get("start")
    end = request.query_params.get("end")
    try:
        return (
            date.fromisoformat(start) if start else today.replace(day=1),
            date.fromisoformat(end) if end else today,
        )
    except ValueError as exc:
        raise ValidationFailed(
            "Dates must be ISO format, for example 2026-09-01.",
            field_errors={"start": ["Invalid date."]},
        ) from exc


class MoneyByCurrencySerializer(serializers.Serializer):
    """Amounts keyed by ISO currency code. Never summed across currencies."""

    amounts = serializers.DictField(child=serializers.CharField())
    record_count = serializers.IntegerField()
    # Distinguishes "no pipeline" from "pipeline we have not valued yet".
    unknown_count = serializers.IntegerField()


class CashReceivedSerializer(serializers.Serializer):
    amounts = serializers.DictField(child=serializers.CharField())
    record_count = serializers.IntegerField()
    note = serializers.CharField()


class OverviewSerializer(serializers.Serializer):
    """Three separate money measures, deliberately without a combined total."""

    period = serializers.DictField(child=serializers.CharField())
    scope = serializers.CharField()
    open_pipeline_value = MoneyByCurrencySerializer()
    won_contract_value = MoneyByCurrencySerializer()
    cash_received = CashReceivedSerializer()
    attention = serializers.DictField(child=serializers.IntegerField())
    basis = serializers.DictField()


class OperationalReportsSerializer(serializers.Serializer):
    conversion_by_source = serializers.DictField()
    stage_aging = serializers.DictField()
    activity_evidence = serializers.DictField()
    delivery_health = serializers.DictField()
    pilot_measures = serializers.DictField()


class OverviewView(WorkspaceAPIView):
    """GET /api/v1/reports/overview (CRM11).

    Returns open pipeline, won contract value and cash received as three
    separate figures, each broken down by currency. There is no combined total,
    deliberately.
    """

    serializer_class = OverviewSerializer

    @extend_schema(
        responses={200: OverviewSerializer},
        summary="Revenue overview",
        parameters=[
            OpenApiParameter("start", str, description="ISO date, inclusive."),
            OpenApiParameter("end", str, description="ISO date, inclusive."),
            OpenApiParameter("currency", str, description="Restrict receipts."),
        ],
    )
    def get(self, request):
        if not has_permission(request.membership, "report.team"):
            raise NotAuthorized("This role may not view management reports.")
        start, end = _period(request)
        return Response(
            metrics.overview(
                membership=request.membership,
                period_start=start,
                period_end=end,
                currency=request.query_params.get("currency"),
            )
        )


class OperationalReportsView(WorkspaceAPIView):
    """GET /api/v1/reports/operational (CRM11)."""

    serializer_class = OperationalReportsSerializer

    @extend_schema(
        responses={200: OperationalReportsSerializer},
        summary="Operational reports",
        parameters=[
            OpenApiParameter("start", str, description="ISO date, inclusive."),
            OpenApiParameter("end", str, description="ISO date, inclusive."),
        ],
    )
    def get(self, request):
        if not has_permission(request.membership, "report.team"):
            raise NotAuthorized("This role may not view management reports.")
        start, end = _period(request)
        return Response(
            {
                "conversion_by_source": metrics.conversion_by_source(
                    membership=request.membership, period_start=start, period_end=end
                ),
                "stage_aging": metrics.stage_aging(membership=request.membership),
                "activity_evidence": metrics.activity_evidence_split(
                    membership=request.membership, period_start=start, period_end=end
                ),
                "delivery_health": metrics.delivery_health(membership=request.membership),
                "pilot_measures": metrics.pilot_measures(membership=request.membership),
            }
        )


class MeasureRecordSerializer(serializers.Serializer):
    type = serializers.CharField()
    id = serializers.CharField()
    label = serializers.CharField()
    detail = serializers.CharField()


class MeasureRecordsSerializer(serializers.Serializer):
    measure = serializers.CharField()
    count = serializers.IntegerField()
    shown = serializers.IntegerField()
    period = serializers.DictField(child=serializers.CharField())
    records = MeasureRecordSerializer(many=True)


class MeasureRecordsView(WorkspaceAPIView):
    """GET /api/v1/reports/records?measure= - the rows behind one figure (CRM11).

    "Every figure shall expose its filter, time period and underlying records."
    The count here is computed from the same definition as the dashboard figure,
    so the two reconcile.
    """

    @extend_schema(
        responses={200: MeasureRecordsSerializer},
        summary="Records behind a report figure",
        parameters=[
            OpenApiParameter("measure", str, required=True),
            OpenApiParameter("start", str, description="ISO date, inclusive."),
            OpenApiParameter("end", str, description="ISO date, inclusive."),
        ],
    )
    def get(self, request):
        if not has_permission(request.membership, "report.team"):
            raise NotAuthorized("This role may not view management reports.")
        start, end = _period(request)
        measure = request.query_params.get("measure", "")
        if measure not in metrics.ATTENTION_MEASURES + metrics.MONEY_MEASURES:
            raise ValidationFailed(
                "Unknown measure.",
                field_errors={
                    "measure": [f"Choose one of the dashboard figures, not {measure!r}."]
                },
            )
        result = metrics.measure_records(
            measure, membership=request.membership, period_start=start, period_end=end
        )
        result["period"] = {"start": start.isoformat(), "end": end.isoformat()}
        return Response(result)


class CashReceiptViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    """The manual receipt register (CRM11).

    Nothing here is created implicitly. Winning a deal does not record money.
    """

    serializer_class = CashReceiptSerializer
    queryset = CashReceipt.objects.select_related("client").all()
    permission_classes = [HasWorkspacePermission]
    required_permission = "receipt.record"
    filterset_fields = ["client", "currency"]

    def create(self, request):
        serializer = CashReceiptCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        workspace_id = request.membership.workspace_id

        client = Client.objects.filter(
            pk=data["client"], workspace_id=workspace_id, deleted_at__isnull=True
        ).first()
        if client is None:
            raise ValidationFailed("That client is not available.")

        opportunity = None
        if data.get("opportunity"):
            opportunity = Opportunity.objects.filter(
                pk=data["opportunity"], workspace_id=workspace_id
            ).first()

        # Mirrors ck_receipt_positive_unless_adjustment, so a bad amount is a
        # 400 the caller can fix rather than a 500 from the constraint.
        if not data.get("adjusts") and data["amount"] <= 0:
            raise ValidationFailed(
                "A receipt must be for a positive amount. To reduce an earlier "
                "receipt, record an adjustment against it.",
                field_errors={"amount": ["Must be greater than zero."]},
            )

        adjusts = None
        if data.get("adjusts"):
            adjusts = CashReceipt.objects.filter(
                pk=data["adjusts"], workspace_id=workspace_id
            ).first()
            if adjusts is None:
                raise ValidationFailed("The receipt being adjusted was not found.")
            if not data.get("adjustment_reason", "").strip():
                raise ValidationFailed(
                    "An adjustment must say why it was made.",
                    field_errors={"adjustment_reason": ["This field is required."]},
                )

        receipt = CashReceipt.objects.create(
            workspace_id=workspace_id,
            client=client,
            opportunity=opportunity,
            amount=data["amount"],
            currency=data["currency"].upper(),
            received_at=data["received_at"],
            evidence_reference=data["evidence_reference"],
            note=data.get("note", ""),
            recorded_by=request.user,
            adjusts=adjusts,
            adjustment_reason=data.get("adjustment_reason", ""),
        )
        record_audit(
            action=AuditAction.CREATE,
            entity_type="reporting.CashReceipt",
            entity_id=receipt.id,
            actor=request.user,
            changes={
                "amount": str(receipt.amount),
                "currency": receipt.currency,
                "evidence_reference": receipt.evidence_reference,
            },
            request_id=getattr(request, "request_id", ""),
        )
        return Response(CashReceiptSerializer(receipt).data, status=status.HTTP_201_CREATED)


class WorkExceptionViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet
):
    """The exception queue (CRM10).

    An employee sees their own exceptions and can explain or dispute them. A
    manager reviews and records a decision. Nothing here triggers an automatic
    consequence.
    """

    serializer_class = WorkExceptionSerializer
    queryset = WorkException.objects.all()
    filterset_fields = ["kind", "state", "subject"]

    def get_queryset(self):
        return exceptions_service.visible_exceptions(self.request.membership)

    @action(detail=True, methods=["post"])
    def explain(self, request, pk=None):
        """The employee's own account of what happened."""
        serializer = ExceptionExplainSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exception = exceptions_service.explain(
            membership=request.membership,
            actor=request.user,
            exception_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(WorkExceptionSerializer(exception).data)

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        serializer = ExceptionReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exception = exceptions_service.review(
            membership=request.membership,
            actor=request.user,
            exception_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(WorkExceptionSerializer(exception).data)


class WorkspaceExportView(WorkspaceAPIView):
    """POST /api/v1/export (CRM13).

    Owner-only, MFA-gated, audited. Returns a ZIP of CSVs with stable UUIDs so
    relationships can be rebuilt outside the system.
    """

    @extend_schema(
        request=None,
        responses={(200, "application/zip"): OpenApiTypes.BINARY},
        summary="Export the workspace",
    )
    def post(self, request):
        payload = export.build_export(
            membership=request.membership,
            actor=request.user,
            request_id=getattr(request, "request_id", ""),
        )
        stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        response = HttpResponse(payload, content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="scalevexo-export-{stamp}.zip"'
        return response
