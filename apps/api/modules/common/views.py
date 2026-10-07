"""Operational probes (SVX-TECH-001 section 12.1)."""

from __future__ import annotations

from django.db import connections
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    checks = serializers.DictField(child=serializers.CharField(), required=False)


class LivenessView(APIView):
    """Is the process up? Must not touch the database.

    A liveness probe that queries the database restarts a healthy application
    whenever the database blips, turning a recoverable fault into an outage.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    serializer_class = HealthSerializer

    @extend_schema(
        responses={200: HealthSerializer},
        summary="Liveness probe",
        auth=[],
    )
    def get(self, request):
        return Response({"status": "alive"})


class ReadinessView(APIView):
    """Can the process serve traffic? Checks the database without leaking detail."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    serializer_class = HealthSerializer

    @extend_schema(
        responses={200: HealthSerializer, 503: HealthSerializer},
        summary="Readiness probe",
        auth=[],
    )
    def get(self, request):
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:  # noqa: BLE001 - deliberate: the detail stays in logs
            return Response(
                {"status": "not_ready", "checks": {"database": "unavailable"}},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "ready", "checks": {"database": "ok"}})
