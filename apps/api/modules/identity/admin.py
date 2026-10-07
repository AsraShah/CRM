"""Django admin is restricted to approved internal operators (section 18.2).

Tenant business models are deliberately not registered here. The admin bypasses
the service layer -- and with it the permission matrix, the audit trail and the
outbox -- so editing a deal through it would produce a change with no evidence
and no downstream automation.

Identity models are exposed read-only for operational diagnosis.
"""

from django.contrib import admin

from modules.identity.models import Membership, User, Workspace


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Workspace)
class WorkspaceAdmin(ReadOnlyAdmin):
    list_display = ("name", "slug", "time_zone", "is_active", "created_at")
    search_fields = ("name", "slug")


@admin.register(Membership)
class MembershipAdmin(ReadOnlyAdmin):
    list_display = ("user", "workspace", "role", "status", "mfa_enrolled")
    list_filter = ("role", "status")
    search_fields = ("user__email",)


@admin.register(User)
class UserAdmin(ReadOnlyAdmin):
    list_display = ("email", "full_name", "is_active", "is_staff", "date_joined")
    search_fields = ("email",)
