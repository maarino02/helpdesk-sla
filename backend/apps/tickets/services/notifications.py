"""Email notifications for status changes and SLA alerts.

Flow (at least once delivery):
1. The service that creates the source row (transition() for a ticket_event,
   the SLA job for an sla_alert) calls notify_on_commit(kind, id).
2. After the commit, the `notify` task reads the source and decides the
   recipients with the data current at that moment.
3. One `send_notification` task per recipient renders and sends one email,
   so an SMTP failure retries only that email.

Only ids travel through the broker: both sources are immutable rows, so the
email describes exactly what happened at commit time, even if the ticket
changed afterwards, and no personal data is stored in Redis.

Known limits, accepted for now: a retry after an email that did go out sends
it twice, and if the broker is down right after the commit the notification
is lost. A transactional outbox (a table written in the same transaction and
drained by a job) would close the second gap.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.tickets.models import (
    AlertKind,
    AlertLevel,
    EventType,
    SlaAlert,
    Status,
    Ticket,
    TicketEvent,
)

STATUS_CHANGE = "status_change"
SLA_ALERT = "sla_alert"
KINDS = (STATUS_CHANGE, SLA_ALERT)

# Status changes the requester hears about: the ones that need their action
# (pending), give them a chance to react (resolved) or end the ticket (closed).
NOTIFIED_STATUSES = frozenset({Status.PENDING, Status.RESOLVED, Status.CLOSED})

# A ContextVar, not a module flag: it only applies to the code running inside
# suppressed(), not to other threads of the same worker or server.
_suppressed: ContextVar[bool] = ContextVar("notifications_suppressed", default=False)


@contextmanager
def suppressed() -> Iterator[None]:
    """Enqueue no notification for the changes made inside this block.

    For writing past history (the demo seed), which nobody must be emailed about.
    """
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)


def notify_on_commit(kind: str, source_id: int) -> None:
    """Enqueue the notification once the current transaction commits.

    Nothing is sent if it rolls back. robust=True: if the broker is down, the
    error is logged instead of raised. The data is already committed, so
    raising would turn a successful request into a 500 for nothing.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown notification kind {kind!r}.")
    # Checked now, not in enqueue(): the commit may happen after the block ends,
    # for example when an outer transaction wraps it.
    if _suppressed.get():
        return

    def enqueue():
        # Imported here: tasks imports the services, which import this module.
        from apps.tickets.tasks import notify

        notify.delay(kind, source_id)

    transaction.on_commit(enqueue, robust=True)


# Recipients


def recipient_ids(kind: str, source_id: int) -> list[int]:
    if kind == STATUS_CHANGE:
        return _status_change_recipients(_event(source_id))
    if kind == SLA_ALERT:
        return _alert_recipients(_alert(source_id))
    raise ValueError(f"Unknown notification kind {kind!r}.")


def _status_change_recipients(event: TicketEvent) -> list[int]:
    if event.event_type != EventType.STATUS_CHANGED or event.to_value not in NOTIFIED_STATUSES:
        return []
    requester = event.ticket.requester
    # Nobody needs an email about what they have just done themselves.
    if not requester.is_active or event.actor_id == requester.pk:
        return []
    return [requester.pk]


def _alert_recipients(alert: SlaAlert) -> list[int]:
    assignee = alert.ticket.assignee
    if assignee is not None and assignee.is_active:
        return [assignee.pk]
    # Unassigned (or assigned to someone who left): every active admin.
    return list(
        User.objects.filter(role=Role.ADMIN, is_active=True)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


# Messages


def build_message(kind: str, source_id: int, recipient_id: int) -> EmailMultiAlternatives:
    """The email for one recipient, as plain text plus an HTML alternative.

    Reads the database: call it inside an RLS context, and send it outside the
    transaction.
    """
    recipient = User.objects.get(pk=recipient_id)
    if kind == STATUS_CHANGE:
        event = _event(source_id)
        template = "status_change"
        context = {
            "ticket": event.ticket,
            "status": Status(event.to_value),
            "auto_closed": event.actor_id is None,
            "auto_close_days": settings.AUTO_CLOSE_AFTER.days,
        }
    elif kind == SLA_ALERT:
        alert = _alert(source_id)
        template = "sla_alert"
        calendar = alert.ticket.organization.business_calendar
        context = {
            "ticket": alert.ticket,
            "kind": AlertKind(alert.kind),
            "breached": alert.level == AlertLevel.BREACHED,
            # In the client's business time zone, the one the deadline refers to.
            # Formatted here: the template |date filter would convert it back to
            # the active time zone (UTC).
            "due_at": alert.due_at.astimezone(ZoneInfo(calendar.timezone)).strftime(
                "%d/%m/%Y %H:%M"
            ),
            "time_zone": calendar.timezone,
            "as_admin": recipient_id != alert.ticket.assignee_id,
        }
    else:
        raise ValueError(f"Unknown notification kind {kind!r}.")

    context["recipient"] = recipient
    context["ticket_url"] = ticket_url(context["ticket"])
    subject = render_to_string(f"tickets/email/{template}_subject.txt", context)
    # Plain text first: clients that cannot show HTML fall back to it, and a
    # message without a text part looks more like spam to filters.
    body = render_to_string(f"tickets/email/{template}_body.txt", context)
    # Autoescaped, unlike the text part: titles and comments are written by users.
    html = render_to_string(f"tickets/email/{template}_body.html", context)
    # A header cannot contain line breaks: the subject template is one line,
    # but the trailing newline of the file must go.
    message = EmailMultiAlternatives(
        subject=" ".join(subject.split()), body=body, to=[recipient.email]
    )
    message.attach_alternative(html, "text/html")
    return message


def ticket_url(ticket: Ticket) -> str:
    """Absolute link to the ticket page. Without a session, the page sends the
    reader to the login, which brings them back here afterwards."""
    return settings.SITE_URL + reverse("web:ticket", args=[ticket.pk])


def _event(event_id: int) -> TicketEvent:
    return TicketEvent.objects.select_related("ticket__organization", "ticket__requester").get(
        pk=event_id
    )


def _alert(alert_id: int) -> SlaAlert:
    return SlaAlert.objects.select_related(
        "ticket__organization__business_calendar", "ticket__assignee"
    ).get(pk=alert_id)
