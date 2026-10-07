"""Authorised reads for work records (section 3.2)."""

from __future__ import annotations

from django.db.models import Q, QuerySet
from django.utils import timezone

from modules.automation.models import Notification, NotificationState
from modules.common.tenancy import current_workspace_id
from modules.identity.models import Membership, Role
from modules.work.models import Task, TaskStatus

MANAGER_ROLES = frozenset({Role.OWNER, Role.ADMIN, Role.SALES_MANAGER, Role.DELIVERY_MANAGER})


def visible_tasks(membership: Membership) -> QuerySet[Task]:
    """Tasks this member may see.

    A manager sees their team's work; everybody else sees their own. Changing a
    URL cannot reach somebody else's task, because the filter is applied here
    rather than assumed by the caller.
    """
    queryset = Task.objects.filter(
        workspace_id=current_workspace_id(), deleted_at__isnull=True
    ).select_related("contact", "opportunity", "owner")

    if membership.role in MANAGER_ROLES:
        if membership.team_id:
            teammates = Membership.objects.filter(
                workspace_id=membership.workspace_id, team_id=membership.team_id
            ).values_list("user_id", flat=True)
            return queryset.filter(Q(owner_id__in=teammates) | Q(owner_id=membership.user_id))
        return queryset
    return queryset.filter(owner_id=membership.user_id)


def today_queues(membership: Membership) -> dict[str, QuerySet]:
    """The four buckets the Today view shows (CRM05).

    Overdue is separated from due-today deliberately: collapsing them hides how
    far behind somebody actually is, which is the one thing the view exists to
    make visible.
    """
    now = timezone.now()
    mine = visible_tasks(membership).filter(owner_id=membership.user_id)

    return {
        "overdue": mine.filter(status=TaskStatus.OPEN, due_at__lt=now).order_by("due_at"),
        "due_now": mine.filter(
            status=TaskStatus.OPEN, due_at__gte=now, due_at__lte=_end_of_local_day(now)
        ).order_by("due_at"),
        "upcoming": mine.filter(status=TaskStatus.OPEN, due_at__gt=_end_of_local_day(now)).order_by(
            "due_at"
        )[:20],
        "blocked": mine.filter(status=TaskStatus.BLOCKED).order_by("due_at"),
    }


def _end_of_local_day(now):
    """End of the viewer's working day.

    The boundary is computed in the workspace zone, then kept as a UTC instant.
    Someone in Karachi should see "today" end at their midnight, not UTC's.
    """
    from datetime import time
    from zoneinfo import ZoneInfo

    from modules.identity.models import Workspace

    workspace = Workspace.objects.filter(pk=current_workspace_id()).first()
    zone = ZoneInfo(workspace.time_zone if workspace else "UTC")
    local = now.astimezone(zone)
    return local.replace(hour=time.max.hour, minute=59, second=59, microsecond=0).astimezone(
        now.tzinfo
    )


def open_notifications(membership: Membership) -> QuerySet[Notification]:
    """Unresolved alerts addressed to this person."""
    return (
        Notification.objects.filter(
            workspace_id=current_workspace_id(),
            recipient_id=membership.user_id,
            resolved_at__isnull=True,
            deleted_at__isnull=True,
        )
        .exclude(state=NotificationState.FAILED)
        .select_related("rule")
        .order_by("-created_at")
    )
