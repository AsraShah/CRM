"""Developer settings. Never used for the pilot server."""

from .base import *  # noqa: F403
from .base import env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

# The SPA runs on its own Vite origin in development. In production the built
# assets are served from the application origin by Caddy, so no CORS relaxation
# is carried into the deployed configuration.
CSRF_TRUSTED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
