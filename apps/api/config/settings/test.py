"""Test settings.

Integration tests run against a real PostgreSQL instance using the ordinary
application role, never SQLite and never a superuser. A superuser would bypass
row-level security and make the isolation tests (TEST02, RD05) meaningless.

Database creation and migration are handled by the ``django_db_setup`` fixture
in ``tests/conftest.py`` rather than by Django's default test runner, because
the two must run as *different* roles: the owner creates and migrates, the
application role runs the assertions.
"""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-for-any-server")
# A valid Fernet key (base64 of "test-only-key-not-for-any-server"). Required
# because DEBUG is off here, so the deployment checks refuse to run without one.
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "dGVzdC1vbmx5LWtleS1ub3QtZm9yLWFueS1zZXJ2ZXI=")
os.environ.setdefault("DATABASE_URL", "postgres://svx_app:svx_app_pw@127.0.0.1:5432/svx_test")
os.environ.setdefault(
    "MIGRATION_DATABASE_URL",
    "postgres://svx_owner:svx_owner_pw@127.0.0.1:5432/svx_test",
)
os.environ.setdefault(
    "SCHEDULER_DATABASE_URL",
    "postgres://svx_scheduler:svx_sched_pw@127.0.0.1:5432/svx_test",
)

from .base import *  # noqa: E402,F403
from .base import DATABASES  # noqa: E402

# The three aliases are three *roles* on one physical database, not three
# databases. Declaring them as mirrors stops the test runner trying to create
# and flush each one separately -- which would also fail, since neither the
# owner nor the scheduler role is meant to truncate application tables.
for _alias in ("owner", "scheduler"):
    if _alias in DATABASES:
        DATABASES[_alias]["TEST"] = {"MIRROR": "default"}

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

# Argon2 is deliberately slow; tests only need a hasher that round-trips.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Throttle and lockout counters must not outlive one test, which a database
# cache outside the test transaction would allow.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

FEATURE_AI_ENABLED = False
FEATURE_EMAIL_CONNECTOR_ENABLED = False
