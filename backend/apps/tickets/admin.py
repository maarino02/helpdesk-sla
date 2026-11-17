from django.contrib import admin

from .models import Category, Ticket


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    """Read-only view of tickets.

    Creating or editing a ticket here would skip the ticket services: per-client
    numbering, SLA deadlines, the state machine and the event history.
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

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
