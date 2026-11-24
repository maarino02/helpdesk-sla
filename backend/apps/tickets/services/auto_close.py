"""Auto-close: resolved tickets that nobody reopened within AUTO_CLOSE_AFTER.

The job reads candidates in batches and closes each one in its own transaction
(see apps.tickets.tasks.auto_close_resolved). Between the read and the close,
the ticket may change: reopened, or reopened and resolved again a minute ago.
So the condition is checked again on the locked row, and the close goes
through transition() like any other status change.
"""

from datetime import datetime

from django.db import transaction
from django.db.models import Q

from apps.tickets.models import Status, Ticket
from apps.tickets.services.state_machine import transition

BATCH_SIZE = 500

# (resolved_at, id) of the last ticket of the previous batch.
Cursor = tuple[datetime, int]


def stale_resolved_batch(
    cutoff: datetime, *, after: Cursor | None = None, limit: int = BATCH_SIZE
) -> list[Cursor]:
    """Up to `limit` tickets resolved at or before `cutoff`, after the cursor.

    Keyset pagination in (resolved_at, id) order, the order of the partial
    index: each batch starts where the previous one ended, so tickets skipped
    by close_if_stale are not read again and the loop always ends.
    """
    queryset = Ticket.objects.filter(status=Status.RESOLVED, resolved_at__lte=cutoff)
    if after is not None:
        last_resolved_at, last_id = after
        # (resolved_at, id) > (last_resolved_at, last_id). The textbook form,
        # a > x OR (a = x AND b > y), gives the planner no range to scan, so
        # every batch would read the index from the beginning. This equivalent
        # form adds a >= x as a bound the index can use. Dropping that bound
        # would be wrong, not just slow: "a > x OR b > y" also matches earlier
        # tickets with a higher id.
        queryset = queryset.filter(
            Q(resolved_at__gte=last_resolved_at)
            & (Q(resolved_at__gt=last_resolved_at) | Q(id__gt=last_id))
        )
    return list(queryset.order_by("resolved_at", "id").values_list("resolved_at", "id")[:limit])


def close_if_stale(ticket_id: int, cutoff: datetime) -> bool:
    """Close the ticket if it is still resolved since `cutoff` or earlier.

    skip_locked: a ticket someone is changing right now is skipped instead of
    waited for; if it is still stale, the next run closes it. The lock taken
    here is the one transition() asks for again, so it does not wait either.
    Returns whether the ticket was closed.
    """
    with transaction.atomic():
        ticket = (
            Ticket.objects.select_for_update(skip_locked=True)
            .filter(pk=ticket_id, status=Status.RESOLVED, resolved_at__lte=cutoff)
            .first()
        )
        if ticket is None:
            return False
        transition(ticket, Status.CLOSED, None)
        return True
