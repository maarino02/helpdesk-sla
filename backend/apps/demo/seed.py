"""The whole seed: catalog, history plan and replay, in one transaction."""

import datetime as dt
from collections import Counter
from dataclasses import dataclass

from django.contrib.auth.hashers import make_password
from django.db.models import Count

from apps.core.rls import system_context
from apps.demo import reset
from apps.demo.bulk import bulk_replay
from apps.demo.catalog import Catalog, create_catalog
from apps.demo.plan import DEFAULT_MONTHS, history, plan_history
from apps.demo.replay import replay
from apps.sla.business_time import Schedule
from apps.sla.models import Priority, SlaPolicy
from apps.sla.services import load_organization_schedule, resolve_policy
from apps.tickets.models import Ticket
from apps.tickets.services.notifications import suppressed


class DataAlreadyExists(Exception):
    pass


@dataclass
class Summary:
    catalog: Catalog
    tickets_by_status: Counter
    alerts: int


@dataclass
class World:
    """What the plan and both replays read from the database, loaded once."""

    schedules: dict[int, Schedule]  # by organization id
    policies: dict[
        tuple[int, str], SlaPolicy
    ]  # the one that applies, by (organization id, priority)

    @property
    def targets(self) -> dict[tuple[int, str], tuple[int, int]]:
        return {
            key: (policy.first_response_minutes, policy.resolution_minutes)
            for key, policy in self.policies.items()
        }


def seed_demo(
    *,
    password: str,
    tickets: int,
    seed: int,
    now: dt.datetime,
    months: int = DEFAULT_MONTHS,
    extra_clients: int = 0,
    bulk: bool = False,
    progress=None,
) -> Summary:
    """Fill an empty database. A failure leaves it as it was (one transaction).

    bulk: write the history in batches (bulk.py) instead of through the services
    (replay.py): the same rows, much faster, for volume seeds. progress(done) is
    called every so many tickets.
    """
    # suppressed(): nobody is emailed about months of made-up history.
    with system_context(), suppressed():
        if reset.has_business_data():
            raise DataAlreadyExists()
        catalog = create_catalog(
            password_hash=make_password(password),
            # Holidays for the whole history and for the months the demo keeps running.
            years=range((now - history(months)).year, now.year + 2),
            extra_clients=extra_clients,
        )
        world = load_world(catalog)
        plans = plan_history(
            catalog,
            world.schedules,
            world.targets,
            count=tickets,
            seed=seed,
            now=now,
            months=months,
        )
        if bulk:
            alerts = bulk_replay(
                plans,
                schedules=world.schedules,
                policies=world.policies,
                now=now,
                progress=progress,
            )
        else:
            alerts = replay(plans, catalog, now=now, progress=progress)
        by_status = Counter(
            dict(Ticket.objects.values_list("status").annotate(Count("id")).order_by())
        )
    return Summary(catalog=catalog, tickets_by_status=by_status, alerts=alerts)


def load_world(catalog: Catalog) -> World:
    organizations = catalog.organizations
    return World(
        schedules={org.pk: load_organization_schedule(org.pk) for org in organizations},
        policies={
            (org.pk, priority): resolve_policy(org, priority)
            for org in organizations
            for priority in Priority.values
        },
    )


def plan_for(catalog: Catalog, *, count: int, seed: int, now: dt.datetime, months=DEFAULT_MONTHS):
    """The history plan, with the calendars and SLA targets read from the database."""
    world = load_world(catalog)
    return plan_history(
        catalog, world.schedules, world.targets, count=count, seed=seed, now=now, months=months
    )
