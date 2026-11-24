"""Emptying the database before seeding it again (seed_demo --reset)."""

from apps.organizations.models import Organization
from apps.sla.models import BusinessCalendar, SlaPolicy
from apps.tickets.models import Category

# Every table with business data, plus the ones pointing to users (admin log,
# group and permission links) and the sessions of the users being deleted.
# Listed explicitly and without CASCADE: if a new table references one of these
# and is missing here, TRUNCATE fails instead of emptying it without anyone
# deciding so. test_reset.py fails for any table of these apps not listed.
TABLES = [
    "sla_alert",
    "ticket_event",
    "ticket_comment",
    "ticket",
    "category",
    "sla_policy",
    "app_user_groups",
    "app_user_user_permissions",
    "django_admin_log",
    "django_session",
    "app_user",
    "organization",
    "holiday",
    "business_hours",
    "business_calendar",
]

# No RESTART IDENTITY: ids keep growing across resets. A JWT names its user by id
# only; with restarted ids, a refresh token issued before the reset (valid for a
# day) would authenticate whoever gets that id afterwards, perhaps an admin.
TRUNCATE = f"TRUNCATE {', '.join(TABLES)}"


def has_business_data() -> bool:
    """Whether a seed would mix with existing data. Users alone do not count:
    a superuser created to explore the admin should not block the first seed."""
    return any(
        model.objects.exists() for model in (Organization, BusinessCalendar, SlaPolicy, Category)
    )


def truncate(connection) -> None:
    """Empty TABLES. Needs the owner: the application role has no TRUNCATE."""
    with connection.cursor() as cursor:
        cursor.execute(TRUNCATE)
