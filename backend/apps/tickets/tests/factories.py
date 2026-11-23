import datetime

import factory
from django.utils import timezone

from apps.accounts.tests.factories import ClientUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.models import Priority, SlaPolicy
from apps.tickets.models import (
    PAUSED_STATUSES,
    AlertKind,
    AlertLevel,
    Category,
    EventType,
    SlaAlert,
    Status,
    Ticket,
    TicketComment,
    TicketEvent,
)


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
    # Real time: the factory skips the services. Tests that check deadlines create
    # tickets with create_ticket, which computes them in business time.
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
    resolution_target_minutes = factory.LazyAttribute(lambda o: o.sla_policy.resolution_minutes)
    # Paused exactly in pending and resolved (ticket_sla_paused_at_matches_status),
    # so any status passed to the factory gives a consistent row. status is not
    # declared (the database default applies), hence getattr.
    sla_paused_at = factory.LazyAttribute(
        lambda o: timezone.now() if getattr(o, "status", None) in PAUSED_STATUSES else None
    )
    first_response_due_at = factory.LazyAttribute(
        lambda o: minutes_after_now(o.sla_policy.first_response_minutes)
    )
    resolution_due_at = factory.LazyAttribute(
        lambda o: minutes_after_now(o.sla_policy.resolution_minutes)
    )
    # 20 % of each budget before its deadline, in real time like the deadlines.
    first_response_warning_at = factory.LazyAttribute(
        lambda o: (
            o.first_response_due_at
            - datetime.timedelta(minutes=o.sla_policy.first_response_minutes // 5)
        )
    )
    resolution_warning_at = factory.LazyAttribute(
        lambda o: o.resolution_due_at - datetime.timedelta(minutes=o.resolution_target_minutes // 5)
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


class TicketCommentFactory(factory.django.DjangoModelFactory):
    """A public comment by the ticket's requester."""

    class Meta:
        model = TicketComment

    ticket = factory.SubFactory(TicketFactory)
    # Copied from the ticket, as the comment service will do.
    organization = factory.SelfAttribute("ticket.organization")
    author = factory.SelfAttribute("ticket.requester")
    body = "Any news on this?"


class TicketEventFactory(factory.django.DjangoModelFactory):
    """A system event (no actor) unless one is passed."""

    class Meta:
        model = TicketEvent

    ticket = factory.SubFactory(TicketFactory)
    organization = factory.SelfAttribute("ticket.organization")
    actor = None
    event_type = EventType.CREATED


class SlaAlertFactory(factory.django.DjangoModelFactory):
    """A resolution warning, with the ticket's current deadline."""

    class Meta:
        model = SlaAlert

    ticket = factory.SubFactory(TicketFactory)
    organization = factory.SelfAttribute("ticket.organization")
    kind = AlertKind.RESOLUTION
    level = AlertLevel.WARNING
    due_at = factory.SelfAttribute("ticket.resolution_due_at")
