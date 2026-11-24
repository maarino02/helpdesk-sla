"""The volume path (bulk.py) must write exactly what the services write.

bulk.py copies the rules of the services. This test replays one plan both ways
and compares every stored row, so a change to a service (a new effect, another
event, a different rounding) fails here until bulk.py follows it.
"""

import datetime as dt
from collections import Counter

import pytest
from django.contrib.auth.hashers import make_password
from django.db import transaction

from apps.accounts.models import User
from apps.demo.bulk import bulk_replay
from apps.demo.catalog import create_catalog
from apps.demo.plan import COMMENT, TRANSITION, UPDATE, Step, TicketPlan
from apps.demo.replay import replay
from apps.demo.seed import load_world, plan_for
from apps.organizations.models import Organization
from apps.sla.models import Priority
from apps.tickets.models import EventType, SlaAlert, Status, Ticket, TicketComment, TicketEvent

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.UTC)
TICKETS = 150


@pytest.fixture
def catalog():
    # Two extra clients: the volume catalog goes through the same paths.
    return create_catalog(
        password_hash=make_password(None), years=range(2025, 2028), extra_clients=2
    )


def plans(catalog):
    # A generator: one fresh, identical plan for each path.
    return plan_for(catalog, count=TICKETS, seed=7, now=NOW)


def snapshot():
    """Every stored row, without ids: tickets by (organization, number)."""
    key = {
        pk: (org, number)
        for pk, org, number in Ticket.objects.values_list("pk", "organization_id", "number")
    }
    ticket_fields = [f.attname for f in Ticket._meta.concrete_fields if f.attname != "id"]
    return {
        "tickets": sorted(Ticket.objects.values_list(*ticket_fields)),
        # In insertion order within each ticket: (created_at, id).
        "events": [
            (key[ticket_id], *rest)
            for ticket_id, *rest in TicketEvent.objects.order_by(
                "ticket_id", "created_at", "id"
            ).values_list(
                "ticket_id",
                "actor_id",
                "event_type",
                "from_value",
                "to_value",
                "metadata",
                "created_at",
            )
        ],
        "comments": [
            (key[ticket_id], *rest)
            for ticket_id, *rest in TicketComment.objects.order_by(
                "ticket_id", "created_at", "id"
            ).values_list("ticket_id", "author_id", "body", "is_internal", "created_at")
        ],
        "alerts": sorted(
            (key[ticket_id], *rest)
            for ticket_id, *rest in SlaAlert.objects.values_list(
                "ticket_id", "kind", "level", "due_at"
            )
        ),
        "next_numbers": sorted(Organization.objects.values_list("pk", "next_ticket_number")),
        "active_users": sorted(User.objects.filter(is_active=True).values_list("pk", flat=True)),
    }


def by_ticket(rows):
    # The ids differ between the two runs, so events are grouped by ticket key
    # and compared in order.
    return sorted(rows, key=lambda row: (row[0], row[-1]))


def test_bulk_writes_the_same_rows_as_the_services(catalog):
    world = load_world(catalog)
    with transaction.atomic():
        alerts = replay(plans(catalog), catalog, now=NOW)
        through_services = snapshot()
        transaction.set_rollback(True)  # back to an empty history

    bulk_alerts = bulk_replay(
        plans(catalog), schedules=world.schedules, policies=world.policies, now=NOW
    )
    in_bulk = snapshot()

    assert len(through_services["tickets"]) == TICKETS
    for table in through_services:
        assert in_bulk[table] == through_services[table], table
    assert bulk_alerts == alerts


def test_the_compared_plan_covers_every_kind_of_step(catalog):
    # The comparison above proves little if the plan never reaches a rule.
    steps = Counter()
    for plan in plans(catalog):
        for step in plan.steps:
            if step.kind == TRANSITION:
                steps[(TRANSITION, step.status, step.actor is None)] += 1
            elif step.kind == UPDATE:
                steps[(UPDATE, *step.changes)] += 1
            else:
                steps[(COMMENT, step.actor.role, step.internal)] += 1

    assert {
        (TRANSITION, Status.PENDING, False),
        (TRANSITION, Status.RESOLVED, False),
        (TRANSITION, Status.OPEN, False),  # reopened by the client
        (TRANSITION, Status.CLOSED, False),  # closed by the client
        (TRANSITION, Status.CLOSED, True),  # auto-close
        (UPDATE, "priority"),
        (UPDATE, "assignee"),
        (COMMENT, "client", False),  # replies that resume a pending ticket
        (COMMENT, "agent", True),  # internal notes
    } <= set(steps)


def test_bulk_inserts_in_batches(catalog, monkeypatch):
    # Several batches, the last one partial: the ticket ids of each batch must
    # still reach its events and comments.
    monkeypatch.setattr("apps.demo.bulk.BATCH_SIZE", 40)
    world = load_world(catalog)
    batches = []

    bulk_replay(
        plans(catalog),
        schedules=world.schedules,
        policies=world.policies,
        now=NOW,
        progress=batches.append,
    )

    assert batches == [40, 80, 120, 150]
    assert not Ticket.objects.exclude(events__event_type=EventType.CREATED).exists()


def test_cases_the_plan_never_produces_fail_instead_of_being_guessed(catalog):
    # bulk.py copies only the priority changes the plan makes. One while the
    # ticket is paused must stop the seed, not be written with an untested rule.
    world = load_world(catalog)
    organization = catalog.organizations[0]
    agent = catalog.agents[0]
    created = dt.datetime(2026, 11, 2, 9, 0, tzinfo=dt.UTC)
    paused_change = TicketPlan(
        organization=organization,
        created_at=created,
        requester=catalog.clients[organization.pk][0],
        category=catalog.categories[0],
        priority=Priority.MEDIUM,
        title="t",
        description="d",
        steps=[
            Step(created + dt.timedelta(minutes=10), COMMENT, agent, body="hola"),
            Step(created + dt.timedelta(minutes=20), TRANSITION, agent, status=Status.PENDING),
            Step(created + dt.timedelta(minutes=30), UPDATE, agent, changes={"priority": "high"}),
        ],
    )

    with pytest.raises(NotImplementedError, match="triage._move_deadlines"):
        bulk_replay([paused_change], schedules=world.schedules, policies=world.policies, now=NOW)
