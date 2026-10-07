"""Work endpoints (CRM05)."""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response

from modules.common.exceptions import NotFound
from modules.common.viewsets import WorkspaceAPIView, WorkspaceScopedViewSet
from modules.crm.models import Contact, Lead, Opportunity
from modules.work import selectors, services
from modules.work.models import Milestone, Project, Task
from modules.work.serializers import (
    TaskCompleteSerializer,
    TaskCreateSerializer,
    TaskRescheduleSerializer,
    TaskSerializer,
    TodaySerializer,
)


def _linked(model, pk, workspace_id, label: str):
    if pk is None:
        return None
    instance = model.objects.filter(
        pk=pk, workspace_id=workspace_id, deleted_at__isnull=True
    ).first()
    if instance is None:
        raise NotFound(f"No {label} matches that identifier.")
    return instance


class TodayView(WorkspaceAPIView):
    """GET /api/v1/today - the work this person can act on now.

    An employee should open this and understand what to do without consulting a
    manager (SVX-PRD-001 section 7.1).
    """

    @extend_schema(responses={200: TodaySerializer}, summary="Work due now")
    def get(self, request):
        membership = request.membership
        queues = selectors.today_queues(membership)
        payload = {
            **{key: list(qs) for key, qs in queues.items()},
            "alerts": list(selectors.open_notifications(membership)[:20]),
            # The browser renders local time from these; the UTC instant is
            # what is stored and compared.
            "server_time": timezone.now(),
            "workspace_time_zone": membership.workspace.time_zone,
        }
        return Response(TodaySerializer(payload).data)


class TaskViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    serializer_class = TaskSerializer
    queryset = Task.objects.all()
    filterset_fields = ["status", "kind", "owner", "opportunity", "project", "milestone"]

    def get_queryset(self):
        return selectors.visible_tasks(self.request.membership).order_by("due_at")

    def create(self, request):
        serializer = TaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        workspace_id = request.membership.workspace_id

        owner = None
        if data.get("owner"):
            from modules.identity.models import User

            owner = User.objects.filter(pk=data["owner"]).first()

        task = services.create_task(
            membership=request.membership,
            actor=request.user,
            title=data["title"],
            due_at=data["due_at"],
            owner=owner,
            kind=data.get("kind"),
            description=data.get("description", ""),
            contact=_linked(Contact, data.get("contact"), workspace_id, "contact"),
            opportunity=_linked(Opportunity, data.get("opportunity"), workspace_id, "opportunity"),
            lead=_linked(Lead, data.get("lead"), workspace_id, "lead"),
            project=_linked(Project, data.get("project"), workspace_id, "project"),
            milestone=_linked(Milestone, data.get("milestone"), workspace_id, "milestone"),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """Complete a task and capture the next step in the same call."""
        serializer = TaskCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        task, next_task = services.complete_task(
            membership=request.membership,
            actor=request.user,
            task_id=pk,
            outcome=data.get("outcome", ""),
            expected_version=data["expected_version"],
            next_action_title=data.get("next_action_title", ""),
            next_action_due_at=data.get("next_action_due_at"),
            stop_reason=data.get("stop_reason", ""),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(
            {
                "task": TaskSerializer(task).data,
                "next_task": TaskSerializer(next_task).data if next_task else None,
            }
        )

    @action(detail=True, methods=["post"])
    def reschedule(self, request, pk=None):
        serializer = TaskRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        task = services.reschedule_task(
            membership=request.membership,
            actor=request.user,
            task_id=pk,
            new_due_at=data["new_due_at"],
            reason=data["reason"],
            expected_version=data["expected_version"],
            request_id=getattr(request, "request_id", ""),
        )
        return Response(TaskSerializer(task).data)
