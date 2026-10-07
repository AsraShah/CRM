from django.apps import AppConfig


class AutomationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.automation"
    label = "automation"
    verbose_name = "Rules, outbox and jobs"
