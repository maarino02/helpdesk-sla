from django.contrib import admin

from apps.core.admin_mixins import AdminRoleRequiredMixin

from .models import Organization


@admin.register(Organization)
class OrganizationAdmin(AdminRoleRequiredMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "business_calendar", "is_active", "created_at")
    list_filter = ("is_active",)
    # One JOIN instead of one query per row for the calendar column.
    list_select_related = ("business_calendar",)
    search_fields = ("name", "slug")

    def get_fields(self, request, obj=None):
        if obj is None:
            # Left out of the add form so that their db_default applies:
            # next_ticket_number starts at 1, and is_active would otherwise be an
            # unchecked checkbox, creating the organization inactive.
            return ("name", "slug", "business_calendar")
        return (
            "name",
            "slug",
            "business_calendar",
            "is_active",
            "next_ticket_number",
            "created_at",
        )

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        # next_ticket_number is owned by the ticket service: editing it could
        # repeat numbers. The slug is part of every ticket reference (ACME-42) the
        # client has already seen, so it is fixed once created.
        return ("slug", "next_ticket_number", "created_at")

    def has_delete_permission(self, request, obj=None):
        # Organizations are deactivated, never deleted (no DELETE privilege).
        return False
