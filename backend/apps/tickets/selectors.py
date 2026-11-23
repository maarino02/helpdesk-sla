"""Reusable ticket reads."""

from django.db.models import QuerySet

from apps.accounts.models import Role
from apps.tickets.models import Ticket, TicketComment, TicketEvent

# "me" and "none" are the special values of the assignee filter.
ASSIGNEE_ME = "me"
ASSIGNEE_NONE = "none"


STAFF = (Role.AGENT, Role.ADMIN)


def tickets_for(user) -> QuerySet[Ticket]:
    """The tickets a user may see, with the relations the API shows.

    Row-level security already hides other organizations' tickets from clients;
    the explicit filter is defense in depth and lets the planner use the indexes
    that start with organization_id (the policy's OR cannot).
    """
    tickets = Ticket.objects.select_related("organization", "category", "requester", "assignee")
    if user.role == Role.CLIENT:
        return tickets.filter(organization_id=user.organization_id)
    if user.role in STAFF:
        return tickets
    # Fail closed for any other role.
    return tickets.none()


def ticket_list(user, *, status=None, priority=None, assignee=None) -> QuerySet[Ticket]:
    """tickets_for() with the list filters. Values arrive already validated."""
    tickets = tickets_for(user)
    if status is not None:
        tickets = tickets.filter(status=status)
    if priority is not None:
        tickets = tickets.filter(priority=priority)
    if assignee == ASSIGNEE_ME:
        tickets = tickets.filter(assignee=user)
    elif assignee == ASSIGNEE_NONE:
        tickets = tickets.filter(assignee__isnull=True)
    elif assignee is not None:
        tickets = tickets.filter(assignee_id=assignee)
    return tickets


def comments_for(user, ticket: Ticket) -> QuerySet[TicketComment]:
    """The comments of a ticket the user may read, with their authors.

    The ticket must come from tickets_for(user). Clients never see internal
    notes: the restrictive row-level security policy already hides them, and
    this filter is defense in depth.
    """
    comments = TicketComment.objects.filter(ticket=ticket).select_related("author")
    if user.role == Role.CLIENT:
        return comments.filter(is_internal=False)
    if user.role in STAFF:
        return comments
    return comments.none()


def events_for(user, ticket: Ticket) -> QuerySet[TicketEvent]:
    """The history of a ticket, with the actors. Clients see it too: it holds
    nothing the ticket itself does not show. The ticket must come from
    tickets_for(user).
    """
    events = TicketEvent.objects.filter(ticket=ticket).select_related("actor")
    if user.role == Role.CLIENT or user.role in STAFF:
        return events
    return events.none()
