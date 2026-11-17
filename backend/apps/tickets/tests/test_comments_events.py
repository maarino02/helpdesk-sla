import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from apps.accounts.tests.factories import AgentUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.tickets.models import EventType, TicketComment
from apps.tickets.tests.factories import (
    TicketCommentFactory,
    TicketEventFactory,
    TicketFactory,
)

pytestmark = pytest.mark.django_db

COMPOSITE_FKS = [
    "ticket_requester_same_organization_fk",
    "ticket_comment_ticket_same_organization_fk",
    "ticket_event_ticket_same_organization_fk",
]


def assert_violates(constraint_name, factory, **kwargs):
    # The database must reject the row; the savepoint keeps the test transaction usable.
    with pytest.raises(IntegrityError, match=constraint_name), transaction.atomic():
        factory(**kwargs)


# Comments


def test_comment_gets_database_defaults():
    comment = TicketCommentFactory()

    assert comment.is_internal is False
    assert comment.created_at is not None


@pytest.mark.parametrize("body", ["", "   ", "\n\t "])
def test_blank_comment_is_rejected(body):
    assert_violates("ticket_comment_body_not_blank", TicketCommentFactory, body=body)


def test_blank_comment_is_also_validated_by_full_clean():
    ticket = TicketFactory()
    comment = TicketComment(
        ticket=ticket, organization=ticket.organization, author=ticket.requester, body=" \n"
    )

    with pytest.raises(ValidationError, match="no puede estar vacío"):
        comment.full_clean()


def test_comment_organization_must_be_the_tickets():
    assert_violates(
        "ticket_comment_ticket_same_organization_fk",
        TicketCommentFactory,
        organization=OrganizationFactory(),
    )


def test_ticket_with_comments_cannot_be_deleted():
    comment = TicketCommentFactory()

    with pytest.raises(ProtectedError):
        comment.ticket.delete()


# Events


def test_event_gets_database_defaults():
    event = TicketEventFactory()

    assert event.metadata == {}
    assert event.created_at is not None


def test_system_event_has_no_actor():
    event = TicketEventFactory(event_type=EventType.SLA_RECALCULATED)

    assert event.actor is None


def test_event_with_actor_and_values():
    event = TicketEventFactory(
        actor=AgentUserFactory(),
        event_type=EventType.STATUS_CHANGED,
        from_value="new",
        to_value="open",
    )

    assert (event.from_value, event.to_value) == ("new", "open")


@pytest.mark.parametrize("field", ["from_value", "to_value"])
def test_empty_string_values_are_rejected(field):
    # NULL is the only "no value"; '' would be a second one.
    assert_violates("ticket_event_values_not_empty", TicketEventFactory, **{field: ""})


def test_invalid_event_type_is_rejected():
    assert_violates("ticket_event_type_valid", TicketEventFactory, event_type="deleted")


@pytest.mark.parametrize("metadata", [[], "text", 42])
def test_metadata_must_be_an_object(metadata):
    assert_violates("ticket_event_metadata_is_object", TicketEventFactory, metadata=metadata)


def test_metadata_cannot_be_null():
    # Django stores Python None as SQL NULL (not the JSON value null), so NOT NULL
    # rejects it before the CHECK is evaluated.
    assert_violates("not-null constraint", TicketEventFactory, metadata=None)


def test_event_organization_must_be_the_tickets():
    assert_violates(
        "ticket_event_ticket_same_organization_fk",
        TicketEventFactory,
        organization=OrganizationFactory(),
    )


# Composite foreign keys: the choices made in the step-7 design, read from the catalog.


@pytest.mark.parametrize("name", COMPOSITE_FKS)
def test_composite_fk_is_immediate_with_no_action(name):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT condeferrable, confupdtype, confdeltype FROM pg_constraint "
            "WHERE conname = %s AND contype = 'f'",
            [name],
        )
        row = cursor.fetchone()

    # 'a' = NO ACTION, for both ON UPDATE and ON DELETE.
    assert row == (False, "a", "a")


@pytest.mark.parametrize("table", ["ticket_comment", "ticket_event"])
def test_no_simple_foreign_key_to_ticket_or_organization(table):
    # The composite FK replaces them (db_constraint=False in the model).
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = %s::regclass AND contype = 'f' "
            "AND confrelid IN ('ticket'::regclass, 'organization'::regclass)",
            [table],
        )
        names = [row[0] for row in cursor.fetchall()]

    assert names == [f"{table}_ticket_same_organization_fk"]


@pytest.mark.parametrize("table", ["ticket_comment", "ticket_event"])
def test_history_index_and_no_single_column_ticket_index(table):
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s", [table])
        indexes = dict(cursor.fetchall())

    assert indexes[f"{table}_ticket_idx"].endswith("USING btree (ticket_id, created_at)")
    assert not any(d.endswith(("(ticket_id)", "(organization_id)")) for d in indexes.values())
