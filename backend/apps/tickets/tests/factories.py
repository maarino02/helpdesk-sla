import datetime

import factory
from django.utils import timezone

from apps.accounts.tests.factories import ClientUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.models import Priority, SlaPolicy
from apps.tickets.models import Category, Status, Ticket


class CategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"Category {n}")


def default_policy(priority):
    # The default policy is unique per priority (NULLS NOT DISTINCT), so tickets
    # share it instead of each one creating its own.
    policy, _ = SlaPolicy.objects.get_or_create(
        organization=None,
        priority=priority,
        defaults={"first_response_minutes": 240, "resolution_minutes": 1440},
    )
    return policy


def minutes_after_now(minutes):
    # Real time, not business time: enough for test data until the SLA engine
    # (phase 5) computes the real deadlines.
    return timezone.now() + datetime.timedelta(minutes=minutes)


class TicketFactory(factory.django.DjangoModelFactory):
    """A new ticket. The traits build consistent resolved and closed tickets."""

    class Meta:
        model = Ticket

    organization = factory.SubFactory(OrganizationFactory)
    number = factory.Sequence(lambda n: n + 1)
    title = factory.Sequence(lambda n: f"Ticket {n}")
    description = "Something does not work."
    category = factory.SubFactory(CategoryFactory)
    priority = Priority.MEDIUM
    # The requester is a client of the same organization as the ticket.
    requester = factory.SubFactory(
        ClientUserFactory, organization=factory.SelfAttribute("..organization")
    )
    sla_policy = factory.LazyAttribute(lambda o: default_policy(o.priority))
    first_response_due_at = factory.LazyAttribute(
        lambda o: minutes_after_now(o.sla_policy.first_response_minutes)
    )
    resolution_due_at = factory.LazyAttribute(
        lambda o: minutes_after_now(o.sla_policy.resolution_minutes)
    )

    class Params:
        resolved = factory.Trait(
            status=Status.RESOLVED,
            resolved_at=factory.LazyFunction(timezone.now),
            resolution_business_minutes=60,
        )
        closed = factory.Trait(
            status=Status.CLOSED,
            resolved_at=factory.LazyFunction(timezone.now),
            resolution_business_minutes=60,
            closed_at=factory.LazyFunction(timezone.now),
        )
