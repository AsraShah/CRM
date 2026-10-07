"""Ticket endpoints (CRM09)."""

from __future__ import annotations

from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response

from modules.common.exceptions import NotFound
from modules.common.viewsets import WorkspaceScopedViewSet
from modules.crm.models import Client
from modules.support import services
from modules.support.models import Ticket
from modules.support.serializers import (
    TicketCommentCreateSerializer,
    TicketCommentSerializer,
    TicketCreateSerializer,
    TicketNextActionSerializer,
    TicketSerializer,
    TicketStateHistorySerializer,
    TicketTransitionSerializer,
)
from modules.work.models import Milestone, Project


def _linked(model, pk, workspace_id, label: str):
    if pk is None:
        return None
    instance = model.objects.filter(
        pk=pk, workspace_id=workspace_id, deleted_at__isnull=True
    ).first()
    if instance is None:
        raise NotFound(f"No {label} matches that identifier.")
    return instance


class TicketViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    serializer_class = TicketSerializer
    queryset = Ticket.objects.select_related("client", "owner", "project").all()
    filterset_fields = ["state", "priority", "client", "owner", "project"]

    def create(self, request):
        serializer = TicketCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        workspace_id = request.membership.workspace_id

        ticket = services.create_ticket(
            membership=request.membership,
            actor=request.user,
            title=data["title"],
            description=data.get("description", ""),
            origin=data["origin"],
            priority=data["priority"],
            client=_linked(Client, data.get("client"), workspace_id, "client"),
            project=_linked(Project, data.get("project"), workspace_id, "project"),
            milestone=_linked(Milestone, data.get("milestone"), workspace_id, "milestone"),
            owner_id=data.get("owner_id"),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(TicketSerializer(ticket).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        """Move a ticket. Waiting and resolution each demand their own evidence."""
        serializer = TicketTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = services.transition_ticket(
            membership=request.membership,
            actor=request.user,
            ticket_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(TicketSerializer(ticket).data)

    @action(detail=True, methods=["post"], url_path="next-action")
    def next_action(self, request, pk=None):
        serializer = TicketNextActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = services.set_next_action(
            membership=request.membership,
            actor=request.user,
            ticket_id=pk,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(TicketSerializer(ticket).data)

    @action(detail=True, methods=["get", "post"])
    def comments(self, request, pk=None):
        """Read or append ticket correspondence.

        Reads go through the same selector the Release 3 client role will use,
        so client visibility is decided in one place rather than in a second
        query somebody has to remember to write correctly.
        """
        ticket = self.get_object()

        if request.method == "GET":
            comments = services.visible_comments(request.membership, ticket)
            return Response(TicketCommentSerializer(comments, many=True).data)

        serializer = TicketCommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = services.add_comment(
            membership=request.membership,
            actor=request.user,
            ticket_id=ticket.id,
            body=serializer.validated_data["body"],
            visibility=serializer.validated_data["visibility"],
            request_id=getattr(request, "request_id", ""),
        )
        return Response(TicketCommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="state-history")
    def state_history(self, request, pk=None):
        ticket = self.get_object()
        history = ticket.state_history.select_related("actor").order_by("changed_at")
        return Response(TicketStateHistorySerializer(history, many=True).data)
