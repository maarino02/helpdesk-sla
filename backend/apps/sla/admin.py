from collections import defaultdict

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from apps.core.admin_mixins import AdminRoleRequiredMixin

from .models import BusinessCalendar, BusinessHours, Holiday, SlaPolicy, Weekday


class BusinessHoursFormSet(BaseInlineFormSet):
    """Reject overlapping slots of one calendar before saving.

    In an inline formset, model validation skips the business_hours_no_overlap
    exclusion constraint: the foreign key to the parent (calendar) is excluded
    from validation, and Django skips every constraint that uses an excluded
    field. Without this check an overlap reaches the INSERT and fails with an
    IntegrityError (a 500). With ATOMIC_REQUESTS the transaction is then aborted,
    so the error cannot be caught to show the form again: it has to be found
    before saving.

    The formset holds every slot of the calendar (existing ones as initial forms),
    so comparing its forms covers new against new and new against existing. The
    constraint stays the final guarantee, e.g. against concurrent edits.
    """

    def clean(self):
        super().clean()
        slots_by_weekday = defaultdict(list)
        for form in self.forms:
            # Forms with their own errors, empty extra forms and forms marked for
            # deletion do not take part.
            if not form.is_valid() or not form.cleaned_data or self._should_delete_form(form):
                continue
            data = form.cleaned_data
            slots_by_weekday[data["weekday"]].append((data["start_time"], data["end_time"]))

        errors = []
        for weekday, slots in sorted(slots_by_weekday.items()):
            slots.sort()
            # Sorted by start, a slot overlaps another one only if it starts before
            # the previous one ends. Slots are [start, end): 09-14 and 14-18 are fine.
            for (prev_start, prev_end), (start, end) in zip(slots, slots[1:], strict=False):
                if start < prev_end:
                    errors.append(
                        f"Las franjas del {Weekday(weekday).label.lower()} se solapan: "
                        f"{prev_start:%H:%M}-{prev_end:%H:%M} y {start:%H:%M}-{end:%H:%M}."
                    )
        if errors:
            raise ValidationError(errors)


class BusinessHoursInline(AdminRoleRequiredMixin, admin.TabularInline):
    model = BusinessHours
    formset = BusinessHoursFormSet
    extra = 1


class HolidayInline(AdminRoleRequiredMixin, admin.TabularInline):
    model = Holiday
    extra = 1


@admin.register(BusinessCalendar)
class BusinessCalendarAdmin(AdminRoleRequiredMixin, admin.ModelAdmin):
    """Calendar with its slots and holidays on one page.

    Changes do not yet move the deadlines of open tickets: that recalculation
    arrives with the SLA engine.
    """

    list_display = ("name", "timezone")
    search_fields = ("name",)
    # The model has no default ordering; without one the paginator warns.
    ordering = ("name",)
    inlines = (BusinessHoursInline, HolidayInline)


@admin.register(SlaPolicy)
class SlaPolicyAdmin(AdminRoleRequiredMixin, admin.ModelAdmin):
    list_display = ("organization", "priority", "first_response_minutes", "resolution_minutes")
    # The organization filter offers an empty choice too: the default policies.
    list_filter = ("priority", "organization")
    list_select_related = ("organization",)
    # organization is the only nullable field shown: NULL means the default policy.
    empty_value_display = "Por defecto"
