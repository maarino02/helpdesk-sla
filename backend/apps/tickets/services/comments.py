"""Ticket comments and their effects on the ticket.

A comment can move the ticket: the first public reply of the staff records the
first response and takes a new ticket (new -> open); a client reply to a pending
ticket resumes it (pending -> open). Status changes still go through
transition(), with the author as the actor.
"""

from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.core.exceptions import ActionNotAllowed, StateConflict
from apps.sla.business_time import business_minutes_between
from apps.sla.services import load_organization_schedule
from apps.tickets.models import Status, Ticket, TicketComment
from apps.tickets.services.notifications import COMMENT, notify_on_commit
from apps.tickets.services.state_machine import transition

STAFF = frozenset({Role.AGENT, Role.ADMIN})


class CommentsClosed(StateConflict):
    code = "comments_closed"

    def __init__(self):
        super().__init__(
            "La incidencia está cerrada y no admite comentarios. Abre una nueva incidencia.",
            current_status=Status.CLOSED,
        )


class InternalNotesStaffOnly(ActionNotAllowed):
    code = "internal_notes_staff_only"

    def __init__(self):
        super().__init__("Solo el personal de soporte puede escribir notas internas.")


class CommentNotAllowed(ActionNotAllowed):
    code = "comment_not_allowed"

    def __init__(self):
        super().__init__("No puedes comentar en esta incidencia.")


def add_comment(
    ticket: Ticket,
    author,
    body: str,
    *,
    is_internal: bool = False,
    now: datetime | None = None,
) -> TicketComment:
    """Add a comment and apply its effects, all or nothing.

    Raises ValidationError for an empty body, CommentsClosed on a closed ticket
    and InternalNotesStaffOnly or CommentNotAllowed for authors who may not
    write it.
    """
    is_staff = author.role in STAFF
    if not body or not body.strip():
        raise ValidationError({"body": "El comentario no puede estar vacío."})
    if is_internal and not is_staff:
        raise InternalNotesStaffOnly()
    # Defense in depth: the API only shows clients their organization's tickets.
    if not is_staff and author.organization_id != ticket.organization_id:
        raise CommentNotAllowed()

    with transaction.atomic():
        # Locked for the first response: two agents replying at once to a new
        # ticket would both see no first response and status new. The second one
        # would overwrite the first response with a later time and then fail on
        # new -> open, with the ticket already open. With the lock it waits, and
        # then sees both changes already made.
        locked = Ticket.objects.select_for_update().get(pk=ticket.pk)
        # After the lock: a comment that waited is never older than the one it
        # waited for, so the thread and the first response stay in order.
        now = now or timezone.now()
        if locked.status == Status.CLOSED:
            raise CommentsClosed()

        comment = TicketComment.objects.create(
            ticket=locked,
            organization_id=locked.organization_id,
            author=author,
            body=body,
            is_internal=is_internal,
            # The same instant as the first response it may record.
            created_at=now,
        )

        if is_internal:
            # Internal notes are invisible to the client: they neither answer it,
            # nor move the ticket, nor notify anyone.
            return comment

        # Registered inside the transaction: if anything below fails (for example
        # the transition), the comment rolls back and nobody is emailed about it.
        notify_on_commit(COMMENT, comment.pk)

        if is_staff:
            if locked.first_response_at is None:
                schedule = load_organization_schedule(locked.organization_id)
                locked.first_response_at = now
                locked.first_response_business_minutes = business_minutes_between(
                    locked.created_at, now, schedule
                )
                locked.save(
                    update_fields=[
                        "first_response_at",
                        "first_response_business_minutes",
                        "updated_at",
                    ]
                )
            if locked.status == Status.NEW:
                # Taking the ticket: transition() also assigns it to the author if
                # nobody has it. It locks the same row again, which does not wait:
                # this transaction already holds the lock.
                transition(locked, Status.OPEN, author, now=now)
        elif locked.status == Status.PENDING:
            # The client answered: the clock resumes.
            transition(locked, Status.OPEN, author, now=now)
        # A client comment on a resolved ticket does not reopen it: a "thanks, it
        # works" must not count against the SLA. Reopening is the explicit
        # resolved -> open transition.

    return comment
