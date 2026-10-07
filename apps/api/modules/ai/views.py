"""AI endpoints (CRM12).

Every response includes the source records the draft was built from, so the
reviewer can compare rather than trust. Schema validity does not establish
factual accuracy (section 10.2).
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from modules.ai import budget, services
from modules.ai.models import AIDraft, DraftPurpose
from modules.common.permissions import IsWorkspaceAdministrator, IsWorkspaceMember
from modules.common.tenancy import workspace_context
from modules.common.viewsets import WorkspaceAPIView, WorkspaceMembershipOnlyMixin
from modules.crm.models import Activity
from modules.crm.serializers import ActivitySerializer


class AIDraftSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIDraft
        fields = [
            "id",
            "purpose",
            "output",
            # Surfaced prominently rather than buried: a confident-sounding
            # draft built on thin evidence is the dangerous one.
            "uncertainties",
            "source_ids",
            "prompt_version",
            "model_name",
            "accepted_at",
            "rejected_at",
            "validation_error",
            "created_at",
        ]
        read_only_fields = fields


class CreateDraftSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=DraftPurpose.choices)
    activity_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=50)
    opportunity_id = serializers.UUIDField(required=False, allow_null=True)
    # Binds a retry to its original reservation, so a repeated request cannot
    # reserve the budget twice.
    request_key = serializers.CharField(max_length=128, required=False)


class DraftDecisionSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class AIDraftViewSet(
    WorkspaceMembershipOnlyMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """POST /api/v1/ai/drafts — request a summary or a follow-up draft.

    Unlike every other viewset, this one does **not** run inside a single
    workspace transaction. It calls an external provider mid-request, and a
    provider timeout must not roll back the budget reservation that exists
    precisely to survive it (section 10.3). Each handler opens its own short
    context instead.
    """

    serializer_class = AIDraftSerializer
    queryset = AIDraft.objects.all()
    permission_classes = [IsWorkspaceMember]
    throttle_scope = "ai"

    def list(self, request, *args, **kwargs):
        with workspace_context(request.membership.workspace_id):
            # A draft is personal working material, not a shared record.
            drafts = list(
                AIDraft.objects.filter(
                    workspace_id=request.membership.workspace_id,
                    requested_by_id=request.membership.user_id,
                    deleted_at__isnull=True,
                ).order_by("-created_at")[:50]
            )
        return Response(AIDraftSerializer(drafts, many=True).data)

    def create(self, request):
        serializer = CreateDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        draft = services.create_draft(
            membership=request.membership,
            actor=request.user,
            workspace_id=request.membership.workspace_id,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )

        # Return the sources alongside the draft. The user reviews them side by
        # side; a draft shown without its sources cannot be checked.
        with workspace_context(request.membership.workspace_id):
            sources = list(
                Activity.objects.filter(
                    id__in=draft.source_ids,
                    workspace_id=request.membership.workspace_id,
                ).select_related("contact", "author")
            )

        return Response(
            {
                "draft": AIDraftSerializer(draft).data,
                "sources": ActivitySerializer(sources, many=True).data,
                "review_note": (
                    "This draft was generated from the sources shown. Check it "
                    "against them before using it. It has not been sent and "
                    "nothing has been changed."
                ),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def decision(self, request, pk=None):
        """Record acceptance or rejection. Feeds the RD08 evaluation."""
        serializer = DraftDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with workspace_context(request.membership.workspace_id):
            draft = services.record_decision(
                membership=request.membership,
                actor=request.user,
                draft_id=pk,
                **serializer.validated_data,
            )
        return Response(AIDraftSerializer(draft).data)


class AIBudgetStatusSerializer(serializers.Serializer):
    """Current spend position. Costs are arithmetic, not measured spend."""

    enabled = serializers.BooleanField()
    period_start = serializers.CharField(required=False)
    ceiling_usd = serializers.CharField()
    reserved_usd = serializers.CharField(required=False)
    settled_usd = serializers.CharField(required=False)
    committed_usd = serializers.CharField()
    remaining_usd = serializers.CharField()
    # Held-but-unreconciled reservations silently shrink the available budget.
    uncertain_reservations = serializers.IntegerField()
    note = serializers.CharField(required=False)


class KillSwitchSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class AIBudgetView(WorkspaceAPIView):
    """GET /api/v1/ai/budget — current spend position.

    POST toggles the administrator kill switch (section 10.3).
    """

    serializer_class = AIBudgetStatusSerializer

    @extend_schema(responses={200: AIBudgetStatusSerializer}, summary="AI budget position")
    def get(self, request):
        return Response(budget.status())

    @extend_schema(
        request=KillSwitchSerializer,
        responses={200: AIBudgetStatusSerializer},
        summary="Toggle the AI kill switch",
    )
    def post(self, request):
        self.permission_classes = [IsWorkspaceAdministrator]
        self.check_permissions(request)
        # Typed, not bool(request.data[...]): the string "false" is truthy, so
        # the old coercion would have switched AI on when asked to switch it off.
        serializer = KillSwitchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        budget.set_kill_switch(
            actor=request.user,
            enabled=serializer.validated_data["enabled"],
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(budget.status())
