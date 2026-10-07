"""Deployment checks (SVX-TECH-001 section 3.3).

These run as part of `manage.py check --deploy` and fail the release pipeline
rather than the first production request. Each one encodes a mistake that is
easy to make and expensive to discover later.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Warning, register


@register("scalevexo")
def check_database_roles(app_configs, **kwargs):
    """The runtime role must not be the role that owns the tables.

    If they are the same, the application can alter its own tables and -- more
    importantly -- row-level security stops being a boundary the application
    cannot cross.
    """
    issues = []
    databases = settings.DATABASES
    app_user = databases.get("default", {}).get("USER")
    owner_user = databases.get("owner", {}).get("USER")

    if not settings.DEBUG and not owner_user:
        issues.append(
            Error(
                "MIGRATION_DATABASE_URL is not configured.",
                hint=(
                    "Migrations must run as a separate owner role that the "
                    "served application does not hold (section 5.3)."
                ),
                id="scalevexo.E001",
            )
        )
    elif owner_user and app_user and owner_user == app_user:
        issues.append(
            Error(
                "The application and migration database roles are identical.",
                hint=(
                    "The runtime role must not own the tables, or FORCE ROW "
                    "LEVEL SECURITY cannot constrain it."
                ),
                id="scalevexo.E002",
            )
        )

    if not settings.DEBUG and settings.SCHEDULER_DB_ALIAS == "default":
        issues.append(
            Warning(
                "SCHEDULER_DATABASE_URL is not configured.",
                hint=(
                    "The scheduler is falling back to the application role, "
                    "which grants it more than the queue metadata it needs "
                    "(section 18.2)."
                ),
                id="scalevexo.W001",
            )
        )
    return issues


@register("scalevexo")
def check_secrets(app_configs, **kwargs):
    issues = []
    if settings.DEBUG:
        return issues

    if len(settings.SECRET_KEY) < 50:
        issues.append(
            Error(
                "DJANGO_SECRET_KEY is too short.",
                hint="Generate one with `secrets.token_urlsafe(64)`.",
                id="scalevexo.E003",
            )
        )
    if not settings.CREDENTIAL_ENCRYPTION_KEY:
        issues.append(
            Error(
                "CREDENTIAL_ENCRYPTION_KEY is not set.",
                hint="Provider credentials must be encrypted at rest.",
                id="scalevexo.E004",
            )
        )
    return issues


@register("scalevexo")
def check_ai_budget(app_configs, **kwargs):
    """The AI ceiling is enforced by the application, not by a provider alert.

    A provider dashboard alert arrives after the spend. The hard stop has to be
    here (section 10.3).
    """
    issues = []
    if not settings.FEATURE_AI_ENABLED:
        return issues

    try:
        ceiling = float(settings.AI_MONTHLY_CEILING_USD)
    except (TypeError, ValueError):
        return [
            Error(
                "AI_MONTHLY_CEILING_USD is not a number.",
                id="scalevexo.E005",
            )
        ]

    if ceiling <= 0:
        issues.append(
            Error(
                "AI is enabled but the monthly ceiling is not positive.",
                hint="Set AI_MONTHLY_CEILING_USD, or disable FEATURE_AI_ENABLED.",
                id="scalevexo.E006",
            )
        )
    if settings.AI_MAX_CONCURRENT_REQUESTS > 1:
        issues.append(
            Warning(
                "More than one concurrent AI request is permitted.",
                hint=(
                    "The pilot reserves cost before dispatch; concurrency above "
                    "one widens the window in which reservations can race."
                ),
                id="scalevexo.W002",
            )
        )
    return issues
