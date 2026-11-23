"""Add the warning instants and fill them for existing tickets.

Nullable here; 0015 makes them NOT NULL and adds their CHECKs and indexes. The
values need business time, computed in Python with apps.sla.business_time (pure
functions, no models: safe to import from a migration, but do not rename them
without taking this migration into account). Schedules are built from the
historical models.

Row-level security is forced, so the owner running the migration needs the
system context to see any row. Every query goes through the migration's own
connection (schema_editor.connection.alias): `migrate --database owner` runs on
the owner's connection while `default` is the application role. A query on
`default` would neither see the system context (it is per connection) nor get
past the lock that ALTER TABLE holds in the migration's transaction: it would
wait forever.
"""

from django.db import migrations, models


def fill_warning_instants(apps, schema_editor):
    from apps.sla.business_time import (
        Schedule,
        Slot,
        business_minutes_between,
        warning_instant,
    )

    db = schema_editor.connection.alias
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.role', 'system', true)")

    Ticket = apps.get_model("tickets", "Ticket")
    BusinessHours = apps.get_model("sla", "BusinessHours")
    Holiday = apps.get_model("sla", "Holiday")
    schedules = {}

    def schedule_of(calendar):
        if calendar.pk not in schedules:
            from zoneinfo import ZoneInfo

            weekly = [[] for _ in range(7)]
            for weekday, start, end in (
                BusinessHours.objects.using(db)
                .filter(calendar=calendar)
                .order_by("weekday", "start_time")
                .values_list("weekday", "start_time", "end_time")
            ):
                weekly[weekday].append(Slot(start, end))
            schedules[calendar.pk] = Schedule(
                timezone=ZoneInfo(calendar.timezone),
                weekly_slots=tuple(tuple(day) for day in weekly),
                holidays=frozenset(
                    Holiday.objects.using(db)
                    .filter(calendar=calendar)
                    .values_list("date", flat=True)
                ),
            )
        return schedules[calendar.pk]

    def warning_or_due(due, budget, schedule):
        # A calendar without working hours (possible in old development data)
        # cannot place a warning: it falls on the deadline itself.
        try:
            return warning_instant(due, budget, schedule)
        except ValueError:
            return due

    tickets = Ticket.objects.using(db).select_related("organization__business_calendar")
    for ticket in tickets.iterator():
        schedule = schedule_of(ticket.organization.business_calendar)
        first_response_budget = business_minutes_between(
            ticket.created_at, ticket.first_response_due_at, schedule
        )
        # A queryset update, not save(): updated_at must not change.
        Ticket.objects.using(db).filter(pk=ticket.pk).update(
            first_response_warning_at=warning_or_due(
                ticket.first_response_due_at, first_response_budget, schedule
            ),
            resolution_warning_at=warning_or_due(
                ticket.resolution_due_at, ticket.resolution_target_minutes, schedule
            ),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("sla", "0005_slots_ending_at_midnight"),
        ("tickets", "0013_event_type_category_changed"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="first_response_warning_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name="ticket",
            name="resolution_warning_at",
            field=models.DateTimeField(null=True),
        ),
        # Nothing to undo: the reverse migration drops the columns.
        migrations.RunPython(fill_warning_instants, migrations.RunPython.noop),
    ]
