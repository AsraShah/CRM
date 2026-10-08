"""Shared Django settings for the ScaleVexo CRM API.

Reference: SVX-TECH-001 sections 1, 3.3, 5 and 6.
Environment-specific modules (local, test, production) import from here and
must not silently relax a security control defined below.
"""

from __future__ import annotations

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parents[2]


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    """Read an environment variable with explicit validation.

    Configuration errors must fail at startup rather than at first request.
    """
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Required environment variable {name} is not set.")
    return value or ""


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
APP_ORIGIN = env("APP_ORIGIN", "http://localhost:5173")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    # ScaleVexo modules (SVX-TECH-001 section 3.1)
    "modules.common",
    "modules.identity",
    "modules.crm",
    "modules.work",
    "modules.automation",
    "modules.support",
    "modules.reporting",
    "modules.integrations",
    "modules.ai",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "modules.common.middleware.RequestIdMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
# "default" is the runtime application role: not the table owner and without
# BYPASSRLS. "owner" is the migration role, used only by
# `manage.py migrate --database=owner`. Separating the two is what makes
# row-level security meaningful (section 5.3, ADR007).
DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", required=True),
        conn_max_age=0,  # Workspace context is transaction-local.
    ),
}
_migration_url = env("MIGRATION_DATABASE_URL")
if _migration_url:
    DATABASES["owner"] = dj_database_url.parse(_migration_url, conn_max_age=0)

# The scheduler enumerates pending jobs across workspaces, which no
# workspace-scoped role can do. Rather than widening the application role, a
# third role is granted queue metadata only -- jobs and outbox events, never CRM
# records (section 18.2). It leases a job and hands the workspace to a scoped
# worker transaction on `default`.
#
# When unset, the scheduler falls back to `default`. That is acceptable for a
# developer machine and is rejected by config.checks in production.
_scheduler_url = env("SCHEDULER_DATABASE_URL")
if _scheduler_url:
    DATABASES["scheduler"] = dj_database_url.parse(_scheduler_url, conn_max_age=0)

SCHEDULER_DB_ALIAS = "scheduler" if _scheduler_url else "default"

# --------------------------------------------------------------------------
# Authentication (SVX-TECH-001 section 5.2)
# --------------------------------------------------------------------------
AUTH_USER_MODEL = "identity.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Invitation-only access: there is no open signup in Release 1 (CRM01).
ACCOUNT_ADAPTER = "modules.identity.adapters.InvitationOnlyAccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
# The user model is identified by email and has no username column. Without
# these two, allauth falls back to `user.username` and every page that displays
# a user raises AttributeError -- which breaks login outright.
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_USER_MODEL_EMAIL_FIELD = "email"
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_RATE_LIMITS = {
    "login_failed": "5/5m/ip,5/5m/key",
    "reset_password": "3/1h/ip",
}
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_RECOVERY_CODE_COUNT = 10
# Roles that must hold a second factor (CRM01).
MFA_REQUIRED_ROLES = ["owner", "admin"]

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 12
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
CSRF_COOKIE_HTTPONLY = False  # Read by the SPA to echo into X-CSRFToken.
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", APP_ORIGIN)

# --------------------------------------------------------------------------
# DRF (SVX-TECH-001 section 6.1)
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "modules.common.authentication.CsrfSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "modules.common.permissions.IsWorkspaceMember",
    ],
    "DEFAULT_PAGINATION_CLASS": "modules.common.pagination.WorkspaceCursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "modules.common.exceptions.scalevexo_exception_handler",
    # Every authenticated caller has a ceiling, whatever the endpoint; the
    # scoped rates add tighter limits to the few expensive actions.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "modules.common.throttling.ActionScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "3000/hour",
        "imports": "10/hour",
        "ai": "20/day",
        "auth": "20/hour",
    },
}

# Throttle counters and allauth's login lockouts live in the cache. The default
# in-process cache would forget them whenever Gunicorn recycles its worker
# (--max-requests), resetting every brute-force limit, so they are kept in
# PostgreSQL like the job queue (ADR002). The table is created by
# common/migrations/0006_cache_table.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "svx_cache",
        "OPTIONS": {"MAX_ENTRIES": 10_000},
    }
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ScaleVexo CRM API",
    "DESCRIPTION": "Internal sales-to-delivery API. Baseline SVX-TECH-001 v1.0.",
    "VERSION": "1.0.0",
    "OAS_VERSION": "3.1.1",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    # Several models have a "state" field with different choices. Name the
    # alert one explicitly so the generated client types stay readable.
    "ENUM_NAME_OVERRIDES": {
        "AlertStateEnum": "modules.automation.models.NotificationState",
    },
}

# --------------------------------------------------------------------------
# Time and localisation
# --------------------------------------------------------------------------
# All instants persist as UTC timestamptz. Display zones never change storage.
TIME_ZONE = "UTC"
USE_TZ = True
USE_I18N = True
LANGUAGE_CODE = "en-gb"
DEFAULT_WORKSPACE_TIME_ZONE = env("DEFAULT_WORKSPACE_TIME_ZONE", "Asia/Karachi")

# --------------------------------------------------------------------------
# Static and private files (SVX-TECH-001 section 12.4)
# --------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Private uploads live outside the web root and are served only through an
# authorised download view using random keys.
PRIVATE_FILE_ROOT = Path(env("PRIVATE_FILE_ROOT", str(BASE_DIR / "private-files")))

# Import limits (section 6.1).
IMPORT_MAX_BYTES = 5 * 1024 * 1024
IMPORT_MAX_ROWS = 10_000
IMPORT_BATCH_SIZE = 100
IMPORT_FILE_RETENTION_DAYS = 7
DIAGNOSTIC_LOG_RETENTION_DAYS = 30
AUDIT_RETENTION_DAYS = 180

# --------------------------------------------------------------------------
# Automation limits (SVX-TECH-001 section 8.3)
# --------------------------------------------------------------------------
RULE_MAX_CHAIN_DEPTH = 3
RULE_MAX_ACTIONS_PER_EXECUTION = 3
JOB_LEASE_SECONDS = 300
JOB_RETRY_DELAYS_MINUTES = [1, 5, 30]
JOB_MAX_ATTEMPTS = 4

# --------------------------------------------------------------------------
# Feature flags and AI budget (SVX-TECH-001 section 10)
# --------------------------------------------------------------------------
FEATURE_AI_ENABLED = env_bool("FEATURE_AI_ENABLED", False)
FEATURE_EMAIL_CONNECTOR_ENABLED = env_bool("FEATURE_EMAIL_CONNECTOR_ENABLED", False)

AI_MONTHLY_CEILING_USD = env("AI_MONTHLY_CEILING_USD", "2.00")
AI_MAX_INPUT_TOKENS = env_int("AI_MAX_INPUT_TOKENS", 4000)
AI_MAX_OUTPUT_TOKENS = env_int("AI_MAX_OUTPUT_TOKENS", 600)
AI_MAX_CONCURRENT_REQUESTS = env_int("AI_MAX_CONCURRENT_REQUESTS", 1)
AI_DAILY_REQUESTS_PER_USER = env_int("AI_DAILY_REQUESTS_PER_USER", 20)
AI_MODEL = env("AI_MODEL", "gpt-4.1-mini-2025-04-14")
AI_INPUT_USD_PER_MTOK = env("AI_INPUT_USD_PER_MTOK", "0.40")
AI_OUTPUT_USD_PER_MTOK = env("AI_OUTPUT_USD_PER_MTOK", "1.60")

CREDENTIAL_ENCRYPTION_KEY = env("CREDENTIAL_ENCRYPTION_KEY")

# --------------------------------------------------------------------------
# Logging (SVX-TECH-001 section 12.1)
# --------------------------------------------------------------------------
# Structured records carry request_id, workspace, job and outcome. They must
# never carry credentials, message bodies or sensitive payloads.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_context": {"()": "modules.common.logging.RequestContextFilter"},
    },
    "formatters": {
        "json": {"()": "modules.common.logging.JsonFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_context"],
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        "scalevexo": {"level": "INFO", "propagate": True},
    },
}
