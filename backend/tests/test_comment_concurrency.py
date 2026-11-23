"""Two agents reply at the same time to a new ticket: real connections and commits.

Without the ticket row lock in add_comment, both would see no first response and
status new: the second would overwrite the first response with a later time and
then fail on new -> open (409) with the ticket already open.
"""

import threading
import time

import pytest
from django.db import connection, transaction

from apps.accounts.tests.factories import AgentUserFactory
from apps.core.rls import activate_user, system_context
from apps.tickets.models import EventType, Status, Ticket, TicketComment, TicketEvent
from apps.tickets.services.comments import add_comment
from apps.tickets.tests.factories import TicketFactory

pytestmark = pytest.mark.usefixtures("production_like")

WAIT = 10  # seconds


def test_two_agents_reply_at_once_to_a_new_ticket():
    with system_context():
        ticket = TicketFactory()
        agents = AgentUserFactory.create_batch(2)
    barrier = threading.Barrier(len(agents))
    errors = []

    def reply(agent):
        try:
            barrier.wait(timeout=WAIT)
            with transaction.atomic():
                activate_user(agent.pk)
                add_comment(ticket, agent, f"Respuesta de {agent.full_name}")
        except Exception as exc:  # Reported by the main thread.
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=reply, args=(agent,)) for agent in agents]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=WAIT)

    assert errors == []
    with system_context():
        updated = Ticket.objects.get(pk=ticket.pk)
        comments = list(TicketComment.objects.filter(ticket=ticket).order_by("created_at"))
        event_types = list(
            TicketEvent.objects.filter(ticket=ticket).values_list("event_type", flat=True)
        )
    assert len(comments) == 2
    first = comments[0]
    # The first response is the earliest comment, and its author took the ticket.
    assert updated.first_response_at == first.created_at
    assert updated.assignee_id == first.author_id
    assert updated.status == Status.OPEN
    assert sorted(event_types) == [EventType.ASSIGNEE_CHANGED, EventType.STATUS_CHANGED]


def waiting_lock_requests():
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_locks WHERE NOT granted")
        return cursor.fetchone()[0]


def test_a_reply_that_waited_is_never_older_than_the_one_it_waited_for():
    # Forces the order that could break it: B starts (and would take its time)
    # while A holds the lock, and A only writes its reply after B is waiting.
    with system_context():
        ticket = TicketFactory()
        first_agent, second_agent = AgentUserFactory.create_batch(2)
    locked, second_waiting = threading.Event(), threading.Event()
    errors = []

    def first():
        try:
            with transaction.atomic():
                activate_user(first_agent.pk)
                Ticket.objects.select_for_update().get(pk=ticket.pk)
                locked.set()
                second_waiting.wait(timeout=WAIT)
                add_comment(ticket, first_agent, "Primero en responder")
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    def second():
        try:
            with transaction.atomic():
                activate_user(second_agent.pk)
                add_comment(ticket, second_agent, "Llego después")
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    first_thread = threading.Thread(target=first)
    first_thread.start()
    assert locked.wait(timeout=WAIT)
    second_thread = threading.Thread(target=second)
    second_thread.start()
    deadline = time.monotonic() + WAIT
    while waiting_lock_requests() == 0:
        assert time.monotonic() < deadline, "the second reply never waited for the lock"
        time.sleep(0.05)
    second_waiting.set()
    first_thread.join(timeout=WAIT)
    second_thread.join(timeout=WAIT)

    assert errors == []
    with system_context():
        updated = Ticket.objects.get(pk=ticket.pk)
        comments = list(TicketComment.objects.filter(ticket=ticket).order_by("created_at"))
    assert [c.author_id for c in comments] == [first_agent.pk, second_agent.pk]
    assert updated.first_response_at == comments[0].created_at
