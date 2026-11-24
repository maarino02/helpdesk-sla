"""The history plan: when tickets arrive and what happens to them. No services run.

The catalog comes from the database (the plan needs real calendars), but the
plan itself only computes.
"""

import datetime as dt
from collections import Counter

import pytest
from django.contrib.auth.hashers import make_password

from apps.accounts.models import Role
from apps.demo.catalog import create_catalog
from apps.demo.plan import (
    COMMENT,
    DEFAULT_MONTHS,
    LAST_TICKET_BEFORE_RETIRING,
    RETIRED_BEFORE_NOW,
    TRANSITION,
    UPDATE,
    history,
)
from apps.demo.seed import load_world, plan_for
from apps.sla.business_time import business_minutes_between
from apps.tickets.models import Status

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def catalog():
    return create_catalog(password_hash=make_password(None), years=range(2024, 2028))


def plan(catalog, count=200, seed=1, now=NOW, months=DEFAULT_MONTHS):
    return list(plan_for(catalog, count=count, seed=seed, now=now, months=months))


def fingerprint(plans):
    return [
        (p.created_at, p.organization.pk, p.title, p.priority)
        + tuple((s.at, s.kind, s.status, s.body) for s in p.steps)
        for p in plans
    ]


def test_same_seed_and_instant_give_the_same_plan(catalog):
    assert fingerprint(plan(catalog)) == fingerprint(plan(catalog))
    assert fingerprint(plan(catalog)) != fingerprint(plan(catalog, seed=2))


def test_tickets_arrive_oldest_first_within_the_last_six_months(catalog):
    plans = plan(catalog)

    created = [p.created_at for p in plans]
    assert len(plans) == 200
    assert created == sorted(created)
    assert created[0] >= NOW - history(DEFAULT_MONTHS) and created[-1] <= NOW


def test_months_set_the_period(catalog):
    created = [p.created_at for p in plan(catalog, months=24)]

    assert NOW - history(24) <= created[0] < NOW - history(23)


def test_activating_people_while_planning_does_not_change_the_plan(catalog):
    # The plan is a generator and the replay activates the retired people and
    # categories while it consumes it: the planner must not see that.
    expected = fingerprint(plan(catalog))
    lazy = plan_for(catalog, count=200, seed=1, now=NOW)
    next(lazy)
    for item in [*catalog.agents, *catalog.categories, *sum(catalog.clients.values(), [])]:
        item.is_active = True

    assert fingerprint([*plan(catalog)[:1], *lazy]) == expected


def test_tickets_are_shared_among_clients(catalog):
    counts = Counter(p.organization.slug for p in plan(catalog))

    assert counts == {"alameda": 90, "pacifico": 70, "vallebueno": 40}


def test_extra_clients_have_a_long_tail():
    catalog = create_catalog(
        password_hash=make_password(None), years=range(2024, 2028), extra_clients=20
    )
    counts = Counter(p.organization.slug for p in plan(catalog, count=4000))

    assert counts.total() == 4000
    # The first extra clients are the big ones; the last ones get a few tickets.
    assert counts["cliente-001"] > 3 * counts["cliente-020"] > 0


def test_steps_are_in_order_and_never_in_the_future(catalog):
    for p in plan(catalog):
        instants = [p.created_at] + [step.at for step in p.steps]
        assert instants == sorted(instants)
        assert instants[-1] <= NOW


def test_recent_tickets_are_cut_at_now(catalog):
    plans = plan(catalog)

    # Old tickets went all the way to closed; the newest ones are still running.
    assert plans[0].steps[-1].status == Status.CLOSED
    assert not any(step.status == Status.CLOSED for step in plans[-1].steps)


def test_first_step_is_a_public_reply_from_staff(catalog):
    # On a new ticket, it takes the ticket: new -> open, assigned to its author.
    for p in plan(catalog):
        if p.steps:
            first = p.steps[0]
            assert first.kind == COMMENT and not first.internal
            assert first.actor.role in (Role.AGENT, Role.ADMIN)


def test_retired_people_and_categories_only_appear_on_old_tickets(catalog):
    cutoff = NOW - RETIRED_BEFORE_NOW - LAST_TICKET_BEFORE_RETIRING
    plans = plan(catalog, count=600)

    def uses_retired(p):
        actors = [step.actor for step in p.steps if step.actor is not None]
        return any(not item.is_active for item in [p.requester, p.category, *actors])

    users = [p for p in plans if uses_retired(p)]
    assert users  # with 600 tickets some old ones use them
    assert all(p.created_at < cutoff for p in users)


def test_auto_close_has_no_actor_and_comes_seven_days_after_resolving(catalog):
    closings = [
        (p.steps[i - 1], step)
        for p in plan(catalog)
        for i, step in enumerate(p.steps)
        if step.kind == TRANSITION and step.status == Status.CLOSED and step.actor is None
    ]

    assert closings
    for resolved, closed in closings:
        assert resolved.status == Status.RESOLVED
        assert dt.timedelta(days=7) < closed.at - resolved.at <= dt.timedelta(days=7, hours=1)


def test_priority_changes_only_before_the_resolution_deadline(catalog):
    # bulk.py relies on it (it does not copy the breached-deadline case).
    world = load_world(catalog)
    changes = [
        (p, step)
        for p in plan(catalog, count=600)
        for step in p.steps
        if step.kind == UPDATE and "priority" in step.changes
    ]

    assert changes
    for p, step in changes:
        schedule = world.schedules[p.organization.pk]
        _, target = world.targets[(p.organization.pk, p.priority)]
        assert business_minutes_between(p.created_at, step.at, schedule) < target
