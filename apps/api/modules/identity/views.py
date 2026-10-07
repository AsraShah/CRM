"""Identity endpoints (CRM01)."""

from __future__ import annotations

from django.contrib.auth import login
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.common import limits
from modules.common.permissions import HasWorkspacePermission, IsWorkspaceMember
from modules.common.viewsets import WorkspaceAPIView, WorkspaceScopedViewSet
from modules.identity import services
from modules.identity.models import Membership, Role, Team
from modules.identity.policy import permissions_for
from modules.identity.serializers import (
    InviteMemberSerializer,
    MembershipSerializer,
    SessionContextSerializer,
    SuspendMemberSerializer,
)


class SessionContextView(WorkspaceAPIView):
    """GET /api/v1/me - who the caller is and what they may do."""

    permission_classes = [IsWorkspaceMember]

    @extend_schema(responses={200: SessionContextSerializer}, summary="Current actor")
    def get(self, request):
        membership = request.membership
        payload = {
            "user_id": request.user.id,
            "email": request.user.email,
            "full_name": request.user.full_name,
            "workspace": membership.workspace,
            "membership_id": membership.id,
            "role": membership.role,
            "team_id": membership.team_id,
            "mfa_enrolled": membership.mfa_enrolled,
            "permissions": permissions_for(membership),
        }
        return Response(SessionContextSerializer(payload).data)


class MembershipViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    """Workspace roster. Suspension revokes access without deleting history."""

    serializer_class = MembershipSerializer
    permission_classes = [IsWorkspaceMember]
    # Declared so the schema generator can resolve the model without a request.
    # It never serves a query; get_queryset() always replaces it.
    queryset = Membership.objects.none()

    def get_queryset(self):
        # The schema generator instantiates the view without a real request.
        if getattr(self, "swagger_fake_view", False):
            return Membership.objects.none()
        # Membership has no soft-delete column, so the shared scoping helper
        # does not apply here.
        return (
            Membership.objects.select_related("user", "team")
            .filter(workspace_id=self.request.membership.workspace_id)
            .order_by("user__email")
        )

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[HasWorkspacePermission],
    )
    def suspend(self, request, pk=None):
        self.required_permission = "membership.manage"
        serializer = SuspendMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.suspend_member(
            actor_membership=request.membership,
            actor=request.user,
            membership_id=pk,
            reason=serializer.validated_data["reason"],
            request_id=getattr(request, "request_id", ""),
        )
        return Response(MembershipSerializer(membership).data)

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[HasWorkspacePermission],
    )
    def reinstate(self, request, pk=None):
        self.required_permission = "membership.manage"
        membership = services.reinstate_member(
            actor_membership=request.membership,
            actor=request.user,
            membership_id=pk,
            request_id=getattr(request, "request_id", ""),
        )
        return Response(MembershipSerializer(membership).data)

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[HasWorkspacePermission],
    )
    def invite(self, request):
        self.required_permission = "invitation.manage"
        serializer = InviteMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team = None
        if serializer.validated_data.get("team"):
            team = Team.objects.filter(
                id=serializer.validated_data["team"],
                workspace_id=request.membership.workspace_id,
            ).first()

        invitation, token = services.invite_member(
            actor_membership=request.membership,
            actor=request.user,
            email=serializer.validated_data["email"],
            role=serializer.validated_data["role"],
            team=team,
            request_id=getattr(request, "request_id", ""),
        )
        # The plaintext token is returned exactly once. It is not logged, and
        # only its hash is stored.
        return Response(
            {
                "invitation_id": invitation.id,
                "email": invitation.email,
                "role": invitation.role,
                "expires_at": invitation.expires_at,
                "token": token,
            },
            status=status.HTTP_201_CREATED,
        )

    def get_permissions(self):
        # required_permission is read by HasWorkspacePermission, and @action
        # sets it after instantiation, so map it here for the detail routes.
        mapping = {
            "suspend": "membership.manage",
            "reinstate": "membership.manage",
            "invite": "invitation.manage",
        }
        if self.action in mapping:
            self.required_permission = mapping[self.action]
        return super().get_permissions()


# ---------------------------------------------------------------------------
# Accepting an invitation. Reached before any membership exists.
# ---------------------------------------------------------------------------


class InvitationAcceptSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200)
    full_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    password = serializers.CharField(
        max_length=256, required=False, allow_blank=True, trim_whitespace=False
    )


class InvitationPreviewSerializer(serializers.Serializer):
    workspace_name = serializers.CharField()
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=Role.choices)
    expires_at = serializers.DateTimeField()
    has_account = serializers.BooleanField()


@method_decorator(ensure_csrf_cookie, name="dispatch")
class InvitationPreviewView(APIView):
    """GET /api/v1/invitations/preview?token= - who invited you to what.

    Also sets the CSRF cookie, which the acceptance POST requires: a first-time
    visitor has never loaded a page that would have set it.
    """

    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        responses={200: InvitationPreviewSerializer},
        summary="Describe a pending invitation",
        parameters=[OpenApiParameter("token", str, required=True)],
    )
    def get(self, request):
        token = request.query_params.get("token", "")
        return Response(InvitationPreviewSerializer(services.describe_invitation(token)).data)


@method_decorator(csrf_protect, name="dispatch")
class InvitationAcceptView(APIView):
    """POST /api/v1/invitations/accept - join the workspace and sign in.

    CSRF is enforced even for an anonymous caller. DRF exempts anonymous
    requests by default, and this one ends in a login: without the check another
    site could sign a visitor into an account the attacker controls.
    """

    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        request=InvitationAcceptSerializer,
        responses={201: MembershipSerializer},
        summary="Accept an invitation",
    )
    def post(self, request):
        serializer = InvitationAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        signed_in = request.user if request.user.is_authenticated else None
        membership = services.accept_invitation(
            token=serializer.validated_data["token"],
            signed_in_user=signed_in,
            full_name=serializer.validated_data.get("full_name", ""),
            password=serializer.validated_data.get("password", ""),
            request_id=getattr(request, "request_id", ""),
        )
        if signed_in is None:
            login(
                request._request,
                membership.user,
                backend="django.contrib.auth.backends.ModelBackend",
            )
        return Response(MembershipSerializer(membership).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Workspace calendar settings (CRM05, CRM06)
# ---------------------------------------------------------------------------


class WorkingIntervalSerializer(serializers.Serializer):
    start = serializers.RegexField(r"^\d{2}:\d{2}$")
    end = serializers.RegexField(r"^\d{2}:\d{2}$")


class WorkspaceSettingsSerializer(serializers.Serializer):
    time_zone = limits.text(64)
    working_weekdays = serializers.ListField(child=serializers.IntegerField(), max_length=7)
    working_intervals = WorkingIntervalSerializer(many=True)
    holidays = serializers.ListField(child=serializers.CharField(max_length=10), max_length=366)
    default_currency = limits.currency()
    calendar_version = serializers.IntegerField(read_only=True)


class WorkspaceSettingsView(WorkspaceAPIView):
    """GET/PUT /api/v1/workspace/settings - the working calendar."""

    @extend_schema(responses={200: WorkspaceSettingsSerializer})
    def get(self, request):
        workspace = request.membership.workspace
        return Response(
            {
                "time_zone": workspace.time_zone,
                "working_weekdays": workspace.working_weekdays or [0, 1, 2, 3, 4],
                # Unset means the engine default, so show exactly that default.
                "working_intervals": workspace.working_intervals
                or [{"start": "09:00", "end": "13:00"}, {"start": "14:00", "end": "18:00"}],
                "holidays": workspace.holidays or [],
                "default_currency": workspace.default_currency,
                "calendar_version": workspace.calendar_version,
            }
        )

    @extend_schema(
        request=WorkspaceSettingsSerializer, responses={200: WorkspaceSettingsSerializer}
    )
    def put(self, request):
        serializer = WorkspaceSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = services.configure_calendar(
            actor_membership=request.membership,
            actor=request.user,
            request_id=getattr(request, "request_id", ""),
            **serializer.validated_data,
        )
        return Response(
            {
                "time_zone": workspace.time_zone,
                "working_weekdays": workspace.working_weekdays,
                "working_intervals": workspace.working_intervals,
                "holidays": workspace.holidays,
                "default_currency": workspace.default_currency,
                "calendar_version": workspace.calendar_version,
            }
        )
