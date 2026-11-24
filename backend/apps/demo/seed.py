"""The whole seed: catalog, history plan and replay, in one transaction."""

import datetime as dt
from collections import Counter
from dataclasses import dataclass

from django.contrib.auth.hashers import make_password

from apps.core.rls import system_context
from apps.demo import reset
from apps.demo.catalog import Catalog, create_catalog
from apps.demo.plan import plan_history
from apps.demo.replay import replay
from apps.sla.models import Priority
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


def seed_demo(*, password: str, tickets: int, seed: int, now: dt.datetime) -> Summary:
    """Fill an empty database. A failure leaves it as it was (one transaction)."""
    # suppressed(): nobody is emailed about months of made-up history.
    with system_context(), suppressed():
        if reset.has_business_data():
            raise DataAlreadyExists()
        catalog = create_catalog(
            password_hash=make_password(password),
            # The history goes back months and the demo keeps running for a while.
            years=range(now.year - 1, now.year + 2),
        )
        plans = plan_for(catalog, count=tickets, seed=seed, now=now)
        alerts = replay(plans, catalog, now=now)
        by_status = Counter(Ticket.objects.values_list("status", flat=True))
    return Summary(catalog=catalog, tickets_by_status=by_status, alerts=alerts)


def plan_for(catalog: Catalog, *, count: int, seed: int, now: dt.datetime):
    """The history plan, with the calendars and SLA targets read from the database."""
    organizations = catalog.organizations
    schedules = {org.pk: load_organization_schedule(org.pk) for org in organizations}
    targets = {
        (org.pk, priority): _minutes(resolve_policy(org, priority))
        for org in organizations
        for priority in Priority.values
    }
    return plan_history(catalog, schedules, targets, count=count, seed=seed, now=now)


def _minutes(policy) -> tuple[int, int]:
    return policy.first_response_minutes, policy.resolution_minutes
