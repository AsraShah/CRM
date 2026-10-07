"""/api/v1 routes (SVX-TECH-001 section 6.1).

State changes use explicit action endpoints rather than unrestricted status
PATCH, so every transition passes through a service that enforces the
invariants.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from modules.ai.views import AIBudgetView, AIDraftViewSet
from modules.automation.views import AlertViewSet, RuleViewSet
from modules.crm.views import (
    ActivityViewSet,
    ContactViewSet,
    ImportBatchViewSet,
    LeadViewSet,
    OpportunityViewSet,
)
from modules.identity.views import (
    InvitationAcceptView,
    InvitationPreviewView,
    MembershipViewSet,
    SessionContextView,
    WorkspaceSettingsView,
)
from modules.reporting.views import (
    CashReceiptViewSet,
    MeasureRecordsView,
    OperationalReportsView,
    OverviewView,
    WorkExceptionViewSet,
    WorkspaceExportView,
)
from modules.support.views import TicketViewSet
from modules.work.delivery_views import ClientViewSet, MilestoneViewSet, ProjectViewSet
from modules.work.views import TaskViewSet, TodayView

router = DefaultRouter()

# Sales (CRM02-CRM04)
router.register("contacts", ContactViewSet, basename="contact")
router.register("leads", LeadViewSet, basename="lead")
router.register("opportunities", OpportunityViewSet, basename="opportunity")
router.register("activities", ActivityViewSet, basename="activity")
router.register("imports", ImportBatchViewSet, basename="import")

# Work and delivery (CRM05, CRM07, CRM08)
router.register("tasks", TaskViewSet, basename="task")
router.register("clients", ClientViewSet, basename="client")
router.register("projects", ProjectViewSet, basename="project")
router.register("milestones", MilestoneViewSet, basename="milestone")

# Support (CRM09)
router.register("tickets", TicketViewSet, basename="ticket")

# Management (CRM10, CRM11)
router.register("exceptions", WorkExceptionViewSet, basename="exception")
router.register("receipts", CashReceiptViewSet, basename="receipt")

# Rules and alerts (CRM06)
router.register("rules", RuleViewSet, basename="rule")
router.register("alerts", AlertViewSet, basename="alert")

# Identity (CRM01)
router.register("memberships", MembershipViewSet, basename="membership")

# Optional assistance (CRM12)
router.register("ai/drafts", AIDraftViewSet, basename="ai-draft")

urlpatterns = [
    path("me/", SessionContextView.as_view(), name="session-context"),
    path("workspace/settings/", WorkspaceSettingsView.as_view(), name="workspace-settings"),
    # Reached before any membership exists, so these are the only anonymous
    # API routes. Both are throttled; accept also enforces CSRF.
    path(
        "invitations/preview/",
        InvitationPreviewView.as_view(),
        name="invitation-preview",
    ),
    path("invitations/accept/", InvitationAcceptView.as_view(), name="invitation-accept"),
    path("today/", TodayView.as_view(), name="today"),
    path("reports/overview/", OverviewView.as_view(), name="report-overview"),
    path("reports/records/", MeasureRecordsView.as_view(), name="report-records"),
    path(
        "reports/operational/",
        OperationalReportsView.as_view(),
        name="report-operational",
    ),
    path("ai/budget/", AIBudgetView.as_view(), name="ai-budget"),
    # Owner-only, MFA-gated, audited (CRM13).
    path("export/", WorkspaceExportView.as_view(), name="workspace-export"),
    path("", include(router.urls)),
]
