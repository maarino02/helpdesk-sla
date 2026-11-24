"""GET /api/analytics/category-trends/: sql/category_trends.sql."""

import datetime as dt

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import AgentUserFactory, ClientUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.tickets.tests.factories import CategoryFactory, TicketFactory

pytestmark = pytest.mark.django_db

URL = reverse("analytics:category-trends")
SPRING = {"start": "2026-03-01", "end": "2026-05-31"}


def created(category, day, count=1, **kwargs):
    at = dt.datetime.combine(day, dt.time(10), dt.UTC)
    for _ in range(count):
        TicketFactory(category=category, created_at=at, **kwargs)


def get(user, **params):
    api = APIClient()
    api.force_authenticate(user)
    return api.get(URL, {**SPRING, **params})


def results(response):
    assert response.status_code == 200, response.json()
    return response.json()["results"]


def cell(rows, month, category):
    [row] = [r for r in rows if r["month"] == month and r["category_id"] == category.pk]
    return row


def test_change_share_and_rank_per_month():
    a = CategoryFactory(name="Acceso")
    b = CategoryFactory(name="Bloqueo")
    c = CategoryFactory(name="Correo")
    created(a, dt.date(2026, 2, 10), 2)  # the month before: compared, not listed
    created(a, dt.date(2026, 3, 10), 4)
    created(b, dt.date(2026, 3, 11), 4)
    created(c, dt.date(2026, 3, 12), 1)
    # Nothing in April: May is compared with April's zero, not with March.
    created(a, dt.date(2026, 5, 5), 1)

    rows = results(get(AgentUserFactory()))

    assert sorted({row["month"] for row in rows}) == ["2026-03-01", "2026-04-01", "2026-05-01"]
    figures = ("tickets", "previous", "change", "change_pct", "share", "rank")

    def at(month, category):
        return tuple(cell(rows, month, category)[key] for key in figures)

    assert at("2026-03-01", a) == (4, 2, 2, 100.0, 44.4, 1)
    assert at("2026-03-01", b) == (4, 0, 4, None, 44.4, 1)  # tie: same rank
    assert at("2026-03-01", c) == (1, 0, 1, None, 11.1, 3)  # 3, not 2
    assert at("2026-04-01", a) == (0, 4, -4, -100.0, None, 1)  # empty month
    assert at("2026-05-01", a) == (1, 0, 1, None, 100.0, 1)
    assert at("2026-05-01", b) == (0, 0, 0, None, 0.0, 2)


def test_rows_come_by_month_then_rank_then_name():
    a, b = CategoryFactory(name="Acceso"), CategoryFactory(name="Bloqueo")
    created(b, dt.date(2026, 3, 10), 2)
    created(a, dt.date(2026, 3, 11), 1)

    rows = results(get(AgentUserFactory()))

    assert [(r["month"], r["category"]) for r in rows] == [
        ("2026-03-01", "Bloqueo"),
        ("2026-03-01", "Acceso"),
        ("2026-04-01", "Acceso"),
        ("2026-04-01", "Bloqueo"),
        ("2026-05-01", "Acceso"),
        ("2026-05-01", "Bloqueo"),
    ]


def test_months_are_those_of_the_provider_time_zone():
    category = CategoryFactory()
    # 22:30 UTC on 31 March is 00:30 on 1 April in Madrid.
    TicketFactory(category=category, created_at=dt.datetime(2026, 3, 31, 22, 30, tzinfo=dt.UTC))

    rows = results(get(AgentUserFactory()))

    assert cell(rows, "2026-03-01", category)["tickets"] == 0
    assert cell(rows, "2026-04-01", category)["tickets"] == 1


def test_the_first_month_is_counted_whole():
    category = CategoryFactory()
    created(category, dt.date(2026, 3, 2))
    created(category, dt.date(2026, 2, 20))

    rows = results(get(AgentUserFactory(), start="2026-03-15"))

    assert (cell(rows, "2026-03-01", category)["tickets"], rows[0]["previous"]) == (1, 1)


def test_inactive_categories_only_while_they_have_tickets():
    active = CategoryFactory()
    retired_with_tickets = CategoryFactory(is_active=False)
    created(retired_with_tickets, dt.date(2026, 4, 1))
    CategoryFactory(is_active=False)

    rows = results(get(AgentUserFactory()))

    assert {row["category_id"] for row in rows} == {active.pk, retired_with_tickets.pk}


def test_staff_can_narrow_to_one_client_and_clients_only_see_theirs():
    category = CategoryFactory()
    mine, theirs = OrganizationFactory(), OrganizationFactory()
    created(category, dt.date(2026, 3, 10), 2, organization=mine)
    created(category, dt.date(2026, 3, 10), 3, organization=theirs)
    customer = ClientUserFactory(organization=mine)

    def march(response):
        return cell(results(response), "2026-03-01", category)["tickets"]

    assert march(get(AgentUserFactory())) == 5
    assert march(get(AgentUserFactory(), organization=mine.pk)) == 2
    assert march(get(customer)) == 2
    assert get(customer, organization=theirs.pk).status_code == 403
