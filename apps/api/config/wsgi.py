"""WSGI entrypoint served by Gunicorn."""

import os

from django.core.wsgi import get_wsgi_application

from config.envfile import load_env

# A no-op in a container, where the orchestrator supplies the environment.
load_env()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_wsgi_application()
