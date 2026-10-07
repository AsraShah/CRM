from django.apps import AppConfig


class IdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.identity"
    label = "identity"
    verbose_name = "Identity and workspaces"

    def ready(self) -> None:
        # Connects the receivers that keep Membership.mfa_enrolled in step with
        # allauth's authenticators.
        from modules.identity import signals  # noqa: F401
