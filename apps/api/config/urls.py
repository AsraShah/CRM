"""Root URL configuration.

The API and the interface share one origin (SVX-TECH-001 "Architecture at a
glance"), so session cookies and CSRF work without cross-origin relaxation.
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from modules.common.views import LivenessView, ReadinessView

urlpatterns = [
    # Operational probes. Liveness must not touch the database; readiness must,
    # without leaking connection details (section 12.1).
    path("healthz", LivenessView.as_view(), name="liveness"),
    path("readyz", ReadinessView.as_view(), name="readiness"),
    # allauth owns login, logout, password reset and MFA pages.
    path("accounts/", include("allauth.urls")),
    path("api/v1/", include("config.api_urls")),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/v1/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="schema-docs",
    ),
    # Restricted to approved internal operators; tenant model editing is
    # disabled in modules.*.admin (section 18.2).
    path("internal-admin/", admin.site.urls),
]
