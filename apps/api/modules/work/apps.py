from django.apps import AppConfig


class WorkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.work"
    label = "work"
    verbose_name = "Tasks, projects and milestones"
