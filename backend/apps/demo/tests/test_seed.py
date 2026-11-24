"""seed_demo without --reset and without tickets: the catalog and the checks before writing.

--reset needs a second connection (the owner) and real commits:
backend/tests/test_seed_reset.py.
"""

import datetime
import io
from zoneinfo import ZoneInfo

import pytest
from django.apps import apps
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.accounts.tests.factories import AdminUserFactory
from apps.demo import reset
from apps.demo.catalog import nth_weekday
from apps.demo.plan import history
from apps.organizations.models import Organization
from apps.sla.business_time import business_minutes_between
from apps.sla.models import Holiday, Priority
from apps.sla.services import load_calendar_schedule, resolve_policy
from apps.tickets.models import Category, Ticket
from apps.tickets.tests.factories import CategoryFactory

pytestmark = pytest.mark.django_db

PASSWORD = "test-demo-password"


@pytest.fixture(autouse=True)
def _demo_password(settings):
    settings.DEMO_PASSWORD = PASSWORD


def seed():
    # The catalog only: the history has its own tests (test_history.py).
    call_command("seed_demo", tickets=0, stdout=io.StringIO())


def organization(slug):
    return Organization.objects.select_related("business_calendar").get(slug=slug)


# What the catalog contains


def test_demo_accounts_log_in_with_the_demo_password():
    seed()

    for email in ["admin@soporte.example", "agente@soporte.example", "cliente@alameda.example"]:
        assert User.objects.get(email=email).check_password(PASSWORD)


def test_roles_and_organizations_follow_the_rules():
    seed()

    staff = User.objects.filter(role__in=[Role.AGENT, Role.ADMIN])
    clients = User.objects.filter(role=Role.CLIENT)
    assert not staff.filter(organization__isnull=False).exists()
    assert {user.organization_id for user in clients} == set(
        Organization.objects.values_list("pk", flat=True)
    )
    for user in clients.select_related("organization"):
        assert user.email.endswith(f"@{user.organization.slug}.example")
    # Only the admin can enter the Django admin.
    assert list(User.objects.filter(is_staff=True).values_list("email", flat=True)) == [
        "admin@soporte.example"
    ]


def test_inactive_people_and_categories_are_included():
    # The history needs them: a former agent assigned to old tickets, a retired
    # category on old tickets, a client user who left.
    seed()

    assert User.objects.filter(role=Role.AGENT, is_active=False).count() == 1
    assert User.objects.filter(role=Role.CLIENT, is_active=False).count() == 1
    assert Category.objects.filter(is_active=False).count() == 1


@pytest.mark.parametrize(
    ("slug", "priority", "expected"),
    [
        ("vallebueno", Priority.CRITICAL, (15, 120)),  # its own agreement
        ("vallebueno", Priority.MEDIUM, (240, 1440)),  # no agreement: default
        ("alameda", Priority.CRITICAL, (30, 240)),
    ],
)
def test_client_agreements_override_the_default_policy(slug, priority, expected):
    seed()

    policy = resolve_policy(organization(slug), priority)
    assert (policy.first_response_minutes, policy.resolution_minutes) == expected


@pytest.mark.parametrize(
    ("slug", "zone", "business_hours_in_week"),
    [
        # Split shift Monday to Thursday (8 h) and 8:00-15:00 on Friday (7 h).
        ("alameda", "Europe/Madrid", 4 * 8 + 7),
        # Monday 16 November 2026 is the Revolution Day holiday (third Monday).
        ("pacifico", "America/Mexico_City", 4 * 9),
        ("vallebueno", "UTC", 7 * 24),
    ],
)
def test_calendars_count_the_expected_business_time(slug, zone, business_hours_in_week):
    seed()
    calendar = organization(slug).business_calendar
    tz = ZoneInfo(zone)
    monday = datetime.datetime(2026, 11, 16, tzinfo=tz)

    minutes = business_minutes_between(
        monday, monday + datetime.timedelta(days=7), load_calendar_schedule(calendar.pk)
    )

    assert calendar.timezone == zone
    assert minutes == business_hours_in_week * 60


def test_holidays_cover_the_history_and_next_year():
    call_command("seed_demo", tickets=0, months=24, stdout=io.StringIO())
    now = timezone.now()

    years = {holiday.date.year for holiday in Holiday.objects.all()}
    assert years == set(range((now - history(24)).year, now.year + 2))


@pytest.mark.parametrize(
    ("year", "month", "n", "expected"),
    [
        (2026, 2, 1, datetime.date(2026, 2, 2)),  # first Monday of February
        (2026, 3, 3, datetime.date(2026, 3, 16)),  # third Monday of March
        (2027, 11, 3, datetime.date(2027, 11, 15)),  # the 1st is a Monday
    ],
)
def test_nth_weekday(year, month, n, expected):
    assert nth_weekday(year, month, 0, n) == expected


# Volume options


def test_extra_clients_cycle_through_the_calendars_and_add_agents():
    call_command("seed_demo", tickets=0, clients=8, stdout=io.StringIO())

    extra = Organization.objects.filter(slug__startswith="cliente-").order_by("slug")
    assert [org.slug for org in extra] == [f"cliente-{i:03}" for i in range(1, 9)]
    assert {org.business_calendar.timezone for org in extra} == {
        "Europe/Madrid",
        "America/Mexico_City",
        "UTC",
    }
    assert all(org.users.count() == 3 for org in extra)
    # One more agent every four extra clients.
    assert User.objects.filter(email__startswith="agente0").count() == 2


def test_bulk_seed_end_to_end():
    out = io.StringIO()

    call_command("seed_demo", tickets=60, bulk=True, clients=2, months=12, stdout=out)

    assert Ticket.objects.count() == 60
    assert Organization.objects.get(slug="alameda").next_ticket_number == (
        Ticket.objects.filter(organization__slug="alameda").count() + 1
    )
    assert "60/60 tickets" in out.getvalue()


# Checks before writing


def test_refuses_to_mix_with_existing_data():
    CategoryFactory()

    with pytest.raises(CommandError, match="--reset"):
        seed()
    assert not Organization.objects.exists()


def test_a_superuser_alone_does_not_block_the_first_seed():
    AdminUserFactory(email="me@helpdesk.local")

    seed()

    assert Organization.objects.count() == 3


def test_refuses_to_run_without_a_password(settings):
    settings.DEMO_PASSWORD = ""

    with pytest.raises(CommandError, match="DEMO_PASSWORD"):
        seed()
    assert not User.objects.exists()


def test_reset_lists_every_table_of_the_business_apps():
    tables = {
        model._meta.db_table
        for label in ("accounts", "organizations", "sla", "tickets")
        # include_auto_created: the many-to-many link tables (app_user_groups...).
        for model in apps.get_app_config(label).get_models(include_auto_created=True)
    }

    assert tables - set(reset.TABLES) == set()
