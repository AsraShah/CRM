"""Rule and alert endpoints (CRM06).

/rules is administrator-only (rule.manage, MFA-gated). /notifications is for
everyone: each person sees and handles the alerts raised for them.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from modules.automation import rules
from modules.automation.models import Notification, RuleDefinition
from modules.common.permissions import HasWorkspacePermission
from modules.common.viewsets import WorkspaceScopedViewSet

# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


class RuleSerializer(serializers.ModelSerializer):
    paused = serializers.SerializerMethodField()

    class Meta:
        model = RuleDefinition
        fields = [
            "id",
            "template",
            "name",
            "rule_version",
            "enabled",
            "paused",
            "paused_at",
            "trigger",
            "conditions",
            "delay",
            "actions",
            "limits",
            "explanation",
            "version",
            "updated_at",
        ]
        read_only_fields = fields

    def get_paused(self, rule: RuleDefinition) -> bool:
        return rule.paused_at is not None


class RuleUpdateSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)
    # Shapes are validated by the closed Pydantic schema in the service.
    conditions = serializers.ListField(child=serializers.DictField(), required=False, max_length=20)
    delay = serializers.DictField(required=False, allow_null=True)
    actions = serializers.ListField(child=serializers.DictField(), required=False, max_length=20)
    limits = serializers.DictField(required=False)
    explanation = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class RuleStateSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)


class RuleSimulateSerializer(serializers.Serializer):
    entity_type = serializers.ChoiceField(
        choices=["lead", "opportunity", "task", "client", "milestone", "ticket"]
    )
    entity_id = serializers.UUIDField()


class AlertSerializer(serializers.ModelSerializer):
    recipient_email = serializers.EmailField(source="recipient.email", read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "recipient",
            "recipient_email",
            "title",
            "body",
            # Why it appeared, and the record it is about (SVX-PRD-001 7.2).
            "cause",
            "entity_type",
            "entity_id",
            "state",
            "acknowledged_at",
            "resolved_at",
            "resolution_note",
            "created_at",
        ]
        read_only_fields = fields


class AlertResolveSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=2000)


class SimulatedConditionSerializer(serializers.Serializer):
    field = serializers.CharField()
    op = serializers.CharField()
    value = serializers.JSONField(allow_null=True)
    actual = serializers.JSONField(allow_null=True)
    matched = serializers.BooleanField()


class RuleSimulationSerializer(serializers.Serializer):
    rule = serializers.CharField()
    conditions = SimulatedConditionSerializer(many=True)
    would_fire = serializers.BooleanField()
    proposed_actions = serializers.ListField(child=serializers.CharField())
    delay = serializers.JSONField(allow_null=True)
    explanation = serializers.CharField()


class CatalogueInstallSerializer(serializers.Serializer):
    installed = serializers.IntegerField()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class RuleViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    """The workspace's current rules. Editing writes a new version."""

    serializer_class = RuleSerializer
    queryset = RuleDefinition.objects.all()
    permission_classes = [HasWorkspacePermission]
    required_permission = "rule.manage"
    pagination_class = None

    def get_queryset(self):
        return rules.current_rules()

    @extend_schema(request=RuleUpdateSerializer, responses={200: RuleSerializer})
    def partial_update(self, request, pk=None):
        serializer = RuleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        expected_version = data.pop("expected_version")
        explanation = data.pop("explanation", None)
        rule = rules.update_rule(
            membership=request.membership,
            actor=request.user,
            rule_id=pk,
            expected_version=expected_version,
            changes=data,
            explanation=explanation,
            request_id=getattr(request, "request_id", ""),
        )
        return Response(RuleSerializer(rule).data)

    def _set_state(self, request, pk, **state):
        serializer = RuleStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = rules.set_rule_state(
            membership=request.membership,
            actor=request.user,
            rule_id=pk,
            expected_version=serializer.validated_data["expected_version"],
            request_id=getattr(request, "request_id", ""),
            **state,
        )
        return Response(RuleSerializer(rule).data)

    @extend_schema(request=RuleStateSerializer, responses={200: RuleSerializer})
    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        return self._set_state(request, pk, enabled=True, paused=False)

    @extend_schema(request=RuleStateSerializer, responses={200: RuleSerializer})
    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        return self._set_state(request, pk, enabled=False)

    @extend_schema(request=RuleStateSerializer, responses={200: RuleSerializer})
    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        """Stop the rule acting, including work it has already queued."""
        return self._set_state(request, pk, paused=True)

    @extend_schema(request=RuleStateSerializer, responses={200: RuleSerializer})
    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        return self._set_state(request, pk, paused=False)

    @extend_schema(request=RuleSimulateSerializer, responses={200: RuleSimulationSerializer})
    @action(detail=True, methods=["post"])
    def simulate(self, request, pk=None):
        """Evaluate against one real record. Read-only; never contacts a provider."""
        serializer = RuleSimulateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            rules.simulate_rule(
                membership=request.membership,
                rule_id=pk,
                entity_type=serializer.validated_data["entity_type"],
                entity_id=serializer.validated_data["entity_id"],
            )
        )

    @extend_schema(request=None, responses={200: CatalogueInstallSerializer})
    @action(detail=False, methods=["post"], url_path="install-catalogue")
    def install_catalogue(self, request):
        """Add any missing catalogue rules, disabled for review."""
        installed = rules.install_catalogue(
            membership=request.membership,
            actor=request.user,
            request_id=getattr(request, "request_id", ""),
        )
        return Response({"installed": installed})


class AlertViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, WorkspaceScopedViewSet):
    """Alerts: each person's own, and the team's for a manager."""

    serializer_class = AlertSerializer
    queryset = Notification.objects.all()
    filterset_fields = ["state", "entity_type"]

    def get_queryset(self):
        return rules.visible_notifications(self.request.membership).select_related("recipient")

    @extend_schema(request=None, responses={200: AlertSerializer})
    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        alert = rules.acknowledge_notification(
            membership=request.membership,
            actor=request.user,
            notification_id=pk,
            request_id=getattr(request, "request_id", ""),
        )
        return Response(AlertSerializer(alert).data)

    @extend_schema(request=AlertResolveSerializer, responses={200: AlertSerializer})
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        serializer = AlertResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        alert = rules.resolve_notification(
            membership=request.membership,
            actor=request.user,
            notification_id=pk,
            note=serializer.validated_data["note"],
            request_id=getattr(request, "request_id", ""),
        )
        return Response(AlertSerializer(alert).data)
