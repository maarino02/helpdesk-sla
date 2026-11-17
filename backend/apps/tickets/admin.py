from django.contrib import admin

from .models import Category, SlaAlert, Ticket


class ReadOnlyAdminMixin:
    """View-only admin for rows that only services and background jobs may write."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Ticket)
class TicketAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Read-only: creating or editing a ticket here would skip the ticket services
    (per-client numbering, SLA deadlines, the state machine and the event history).
    """

    list_display = (
        "__str__",
        "title",
        "status",
        "priority",
        "organization",
        "assignee",
        "created_at",
    )
    list_filter = ("status", "priority", "category")
    # __str__ uses the organization slug: one JOIN instead of one query per row.
    list_select_related = ("organization", "assignee")
    search_fields = ("title",)


@admin.register(SlaAlert)
class SlaAlertAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Read-only: alerts are raised by the SLA job (phase 6)."""

    list_display = ("ticket", "kind", "level", "due_at", "created_at")
    list_filter = ("kind", "level")
    # Ticket.__str__ needs the ticket and its organization.
    list_select_related = ("ticket__organization",)
