from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.common"
    label = "common"
    verbose_name = "Shared infrastructure"

    def ready(self) -> None:
        # Registers the deployment checks so `check --deploy` runs them, and
        # the OpenAPI extensions so the schema describes every endpoint.
        from config import checks  # noqa: F401
        from modules.common import schema  # noqa: F401
