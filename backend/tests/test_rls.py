"""Row-level security: context functions and the policies on client tables.

Each test starts with the system context (backend/conftest.py), creates its rows,
and then switches to the context under test with set_config(..., true).
"""

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction

from apps.organizations.tests.factories import OrganizationFactory
from apps.tickets.models import SlaAlert, Ticket, TicketComment
from apps.tickets.tests.factories import (
    SlaAlertFactory,
    TicketCommentFactory,
    TicketFactory,
)

pytestmark = pytest.mark.django_db

STAFF_ROLES = ["agent", "admin", "system"]


def set_context(role=None, org_id=None):
    # '' is how a missing value reads on a reused connection; the functions
    # must treat it as NULL.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.role', %s, true), set_config('app.org_id', %s, true)",
            [role or "", str(org_id or "")],
        )


def fetchone(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()


# Context functions


def test_context_functions_read_the_transaction_settings():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.role', 'client', true),"
            " set_config('app.org_id', '7', true),"
            " set_config('app.user_id', '9', true)"
        )

    assert fetchone("SELECT app_role(), app_org_id(), app_user_id()") == ("client", 7, 9)


def test_context_functions_treat_empty_settings_as_null():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.role', '', true),"
            " set_config('app.org_id', '', true),"
            " set_config('app.user_id', '', true)"
        )

    assert fetchone("SELECT app_role(), app_org_id(), app_user_id()") == (None, None, None)


@pytest.mark.django_db(transaction=True)
def test_context_functions_return_null_when_never_set():
    connection.close()  # A new session, where the settings were never defined.

    assert fetchone("SELECT app_role(), app_org_id(), app_user_id()") == (None, None, None)


# Tickets


def test_without_context_nothing_is_visible():
    TicketFactory()
    set_context(None)

    assert Ticket.objects.count() == 0


def test_client_sees_only_tickets_of_their_organization():
    own = TicketFactory()
    TicketFactory()  # Another organization.
    set_context("client", own.organization_id)

    assert list(Ticket.objects.values_list("id", flat=True)) == [own.id]


def test_client_without_organization_sees_nothing():
    TicketFactory()
    set_context("client", None)

    assert Ticket.objects.count() == 0


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_staff_and_system_see_every_organization(role):
    TicketFactory.create_batch(2)
    set_context(role)

    assert Ticket.objects.count() == 2


@pytest.mark.parametrize("role", ["", "login", "superuser", "Client"])
def test_unknown_roles_see_nothing(role):
    TicketFactory()
    set_context(role)

    assert Ticket.objects.count() == 0


def test_client_cannot_move_a_ticket_to_another_organization():
    ticket = TicketFactory()
    other = OrganizationFactory()
    set_context("client", ticket.organization_id)

    # WITH CHECK defaults to the USING expression: the new row must be visible too.
    with pytest.raises(ProgrammingError, match="row-level security"), transaction.atomic():
        Ticket.objects.filter(pk=ticket.pk).update(organization=other)


# Comments


def test_client_never_sees_internal_notes():
    ticket = TicketFactory()
    public = TicketCommentFactory(ticket=ticket)
    TicketCommentFactory(ticket=ticket, is_internal=True)
    set_context("client", ticket.organization_id)

    assert list(TicketComment.objects.values_list("id", flat=True)) == [public.id]


def test_agent_sees_internal_notes():
    ticket = TicketFactory()
    TicketCommentFactory(ticket=ticket)
    TicketCommentFactory(ticket=ticket, is_internal=True)
    set_context("agent")

    assert TicketComment.objects.count() == 2


def test_client_cannot_write_internal_notes():
    ticket = TicketFactory()
    set_context("client", ticket.organization_id)

    with pytest.raises(ProgrammingError, match="row-level security"), transaction.atomic():
        TicketComment.objects.create(
            ticket=ticket,
            organization_id=ticket.organization_id,
            author=ticket.requester,
            body="Hidden?",
            is_internal=True,
        )


def test_client_cannot_comment_on_another_organizations_ticket():
    own = TicketFactory()
    foreign = TicketFactory()
    set_context("client", own.organization_id)

    # With the foreign organization: rejected by the policy.
    with pytest.raises(ProgrammingError, match="row-level security"), transaction.atomic():
        TicketComment.objects.create(
            ticket=foreign,
            organization_id=foreign.organization_id,
            author=own.requester,
            body="Hello",
        )
    # With their own organization: foreign key checks bypass row-level security,
    # but the composite foreign key (ticket_id, organization_id) rejects it.
    with pytest.raises(IntegrityError), transaction.atomic():
        TicketComment.objects.create(
            ticket=foreign,
            organization_id=own.organization_id,
            author=own.requester,
            body="Hello",
        )


# SLA alerts


def test_client_never_sees_sla_alerts():
    alert = SlaAlertFactory()
    set_context("client", alert.organization_id)

    assert SlaAlert.objects.count() == 0


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_staff_and_system_see_sla_alerts(role):
    SlaAlertFactory()
    set_context(role)

    assert SlaAlert.objects.count() == 1


# Query plans


def test_application_filter_uses_the_index_under_row_level_security():
    # The policy is an OR of the staff and client branches, so the planner
    # cannot turn it into an index condition: it is applied as a filter. The
    # explicit organization filter the application adds (defense in depth) is
    # what lets PostgreSQL use the index.
    set_context("client", 1)
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")  # Tables are tiny in tests.
        cursor.execute(
            "EXPLAIN (COSTS OFF)"
            " SELECT id FROM ticket WHERE organization_id = 1 AND status = 'open'"
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "ticket_org_status_idx" in plan
    assert "Index Cond: ((organization_id = 1)" in plan
    assert "current_setting('app.role'" in plan  # The policy is still applied.
