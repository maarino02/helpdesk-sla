from django.contrib import admin

from apps.core.admin_mixins import AdminRoleRequiredMixin, report_recalculated_deadlines
from apps.sla.services import lock_and_snapshot
from apps.tickets.services.sla_recalculation import (
    recalculate_deadlines,
    tickets_of_organization,
)

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

    def save_model(self, request, obj, form, change):
        """Switching calendars reprojects the organization's open tickets (ADR-0005)."""
        if not (change and "business_calendar" in form.changed_data):
            super().save_model(request, obj, form, change)
            return
        old_id = Organization.objects.values_list("business_calendar_id", flat=True).get(pk=obj.pk)
        new_id = obj.business_calendar_id
        # Both calendars locked, in id order: two switches in opposite directions
        # (X from A to B, Y from B to A) taking them in another order could each
        # wait for the other forever.
        snapshots = {cid: lock_and_snapshot(cid) for cid in sorted({old_id, new_id})}
        super().save_model(request, obj, form, change)
        moved = recalculate_deadlines(
            tickets_of_organization(obj.pk),
            snapshots[old_id],
            snapshots[new_id],
            context={"organization_calendar": {"from": old_id, "to": new_id}},
        )
        report_recalculated_deadlines(self, request, moved)

    def has_delete_permission(self, request, obj=None):
        # Organizations are deactivated, never deleted (no DELETE privilege).
        return False
