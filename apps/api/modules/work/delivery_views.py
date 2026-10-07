"""Client, project and milestone endpoints (CRM07, CRM08)."""

from __future__ import annotations

from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response

from modules.common.viewsets import WorkspaceScopedViewSet
from modules.crm import conversion
from modules.crm.models import Client
from modules.work import delivery
from modules.work.delivery_serializers import (
    ClientSerializer,
    HandoverDecisionSerializer,
    HandoverReturnSerializer,
    MilestoneAcceptSerializer,
    MilestoneBlockSerializer,
    MilestoneCancelSerializer,
    MilestoneDependencySerializer,
    MilestoneReopenSerializer,
    MilestoneReturnSerializer,
    MilestoneSerializer,
    MilestoneSubmitSerializer,
    MilestoneVersionSerializer,
    ProjectSerializer,
    ScopeChangeCreateSerializer,
    ScopeChangeSerializer,
)
from modules.work.models import Milestone, Project


class ClientViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    """Clients and the handover decision (CRM07)."""

    serializer_class = ClientSerializer
    queryset = Client.objects.select_related("contact", "company", "delivery_owner").all()
    filterset_fields = ["status", "delivery_owner"]

    @action(detail=True, methods=["post"], url_path="accept-handover")
    def accept_handover(self, request, pk=None):
        """Delivery accepts. Separate from project creation, and visible to sales."""
        serializer = HandoverDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client = conversion.accept_handover(
            membership=request.membership,
            actor=request.user,
            client_id=pk,
            expected_version=serializer.validated_data["expected_version"],
            note=serializer.validated_data.get("note", ""),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(ClientSerializer(client).data)

    @action(detail=True, methods=["post"], url_path="return-handover")
    def return_handover(self, request, pk=None):
        """Delivery returns it, naming specifically what is missing."""
        serializer = HandoverReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client = conversion.return_handover(
            membership=request.membership,
            actor=request.user,
            client_id=pk,
            expected_version=serializer.validated_data["expected_version"],
            missing_information=serializer.validated_data["missing_information"],
            request_id=getattr(request, "request_id", ""),
        )
        return Response(ClientSerializer(client).data)


class ProjectViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    serializer_class = ProjectSerializer
    queryset = Project.objects.select_related("client").prefetch_related(
        "milestones__dependencies__depends_on"
    )
    filterset_fields = ["status", "client", "owner"]

    @action(detail=True, methods=["get", "post"], url_path="scope-changes")
    def scope_changes(self, request, pk=None):
        project = self.get_object()

        if request.method == "GET":
            changes = project.scope_changes.order_by("-recorded_at")
            return Response(ScopeChangeSerializer(changes, many=True).data)

        serializer = ScopeChangeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change = delivery.record_scope_change(
            membership=request.membership,
            actor=request.user,
            project_id=project.id,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(ScopeChangeSerializer(change).data, status=status.HTTP_201_CREATED)


class MilestoneViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    """Milestone lifecycle.

    Submission and acceptance are separate endpoints, not one status PATCH,
    because they are separate acts by separate people (CRM08).
    """

    serializer_class = MilestoneSerializer
    queryset = Milestone.objects.select_related("project", "owner").prefetch_related(
        "dependencies__depends_on"
    )
    filterset_fields = ["status", "project", "owner"]

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        """Begin work. Conversion creates milestones planned, not started."""
        serializer = MilestoneVersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        milestone = delivery.start_milestone(
            membership=request.membership,
            actor=request.user,
            milestone_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(MilestoneSerializer(milestone).data)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        serializer = MilestoneSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        milestone = delivery.submit_milestone(
            membership=request.membership,
            actor=request.user,
            milestone_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(MilestoneSerializer(milestone).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        serializer = MilestoneAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        milestone = delivery.accept_milestone(
            membership=request.membership,
            actor=request.user,
            milestone_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(MilestoneSerializer(milestone).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        serializer = MilestoneReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        milestone = delivery.reopen_milestone(
            membership=request.membership,
            actor=request.user,
            milestone_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(MilestoneSerializer(milestone).data)

    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        serializer = MilestoneBlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        milestone = delivery.block_milestone(
            membership=request.membership,
            actor=request.user,
            milestone_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(MilestoneSerializer(milestone).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel with a reason and its impact. Accepted work cannot be cancelled."""
        serializer = MilestoneCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        milestone = delivery.cancel_milestone(
            membership=request.membership,
            actor=request.user,
            milestone_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(MilestoneSerializer(milestone).data)

    @action(detail=True, methods=["post"], url_path="send-back")
    def send_back(self, request, pk=None):
        """A reviewer returns submitted work with what must change."""
        serializer = MilestoneReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        milestone = delivery.return_milestone(
            membership=request.membership,
            actor=request.user,
            milestone_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(MilestoneSerializer(milestone).data)

    @action(detail=True, methods=["post"], url_path="dependencies")
    def add_dependency(self, request, pk=None):
        serializer = MilestoneDependencySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        depends_on = serializer.validated_data["depends_on"]
        delivery.add_dependency(
            membership=request.membership,
            actor=request.user,
            milestone_id=pk,
            depends_on_id=depends_on,
            request_id=getattr(request, "request_id", ""),
        )
        milestone = self.get_object()
        return Response(MilestoneSerializer(milestone).data, status=status.HTTP_201_CREATED)
