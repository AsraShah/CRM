"""Identity serializers (CRM01)."""

from __future__ import annotations

from rest_framework import serializers

from modules.common import limits
from modules.identity.models import Membership, Role, Workspace


class WorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = [
            "id",
            "name",
            "slug",
            "time_zone",
            "calendar_version",
            "default_currency",
        ]
        read_only_fields = fields


class MembershipSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True, default=None)

    class Meta:
        model = Membership
        fields = [
            "id",
            # The user id, which is what owner and assignee fields take. The
            # membership id alone cannot fill an owner picker.
            "user",
            "email",
            "full_name",
            "role",
            "status",
            "team",
            "team_name",
            "mfa_enrolled",
            "is_available_for_assignment",
            "suspended_at",
            "created_at",
        ]
        read_only_fields = fields


class SessionContextSerializer(serializers.Serializer):
    """What the SPA needs to render its navigation for this actor."""

    user_id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name = serializers.CharField(allow_blank=True)
    workspace = WorkspaceSerializer()
    membership_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=Role.choices)
    team_id = serializers.UUIDField(allow_null=True)
    mfa_enrolled = serializers.BooleanField()
    # Drives which actions the interface offers. The server re-checks every
    # call; this only avoids presenting an action that would be refused.
    permissions = serializers.ListField(child=serializers.CharField())


class InviteMemberSerializer(serializers.Serializer):
    email = limits.email()
    role = serializers.ChoiceField(choices=Role.choices)
    team = serializers.UUIDField(required=False, allow_null=True)


class SuspendMemberSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)
