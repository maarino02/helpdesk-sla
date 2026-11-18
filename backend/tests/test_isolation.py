"""Tenant isolation: what each context sees in each table.

One dataset with two organizations, A and B, and every kind of row. For each
context, EXPECTED lists the labels of the rows it must see in every table:
anything more is a leak, anything less a broken feature. Queries are plain SQL
as helpdesk_app, so the test checks the database without the ORM in between.

Writes are covered rule by rule in test_rls.py and test_rls_users_and_catalogs.py.
"""

import pytest
from django.db import connection

from apps.accounts.tests.factories import (
    AdminUserFactory,
    AgentUserFactory,
    ClientUserFactory,
)
from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.models import Priority
from apps.sla.tests.factories import (
    BusinessCalendarFactory,
    BusinessHoursFactory,
    HolidayFactory,
    SlaPolicyFactory,
)
from apps.tickets.tests.factories import (
    CategoryFactory,
    SlaAlertFactory,
    TicketCommentFactory,
    TicketEventFactory,
    TicketFactory,
    default_policy,
)
from tests.rls import set_context

pytestmark = pytest.mark.django_db

TABLES = (
    "ticket",
    "ticket_comment",
    "ticket_event",
    "sla_alert",
    "organization",
    "app_user",
    "category",
    "business_calendar",
    "business_hours",
    "holiday",
    "sla_policy",
)

NOTHING = {table: set() for table in TABLES}

CATALOGS = {
    "category": {"category"},
    "business_calendar": {"calendar"},
    "business_hours": {"hours"},
    "holiday": {"holiday"},
}

STAFF = {
    "ticket": {"A.ticket", "B.ticket"},
    "ticket_comment": {"A.comment", "A.internal_note", "B.comment", "B.internal_note"},
    "ticket_event": {"A.event", "B.event"},
    "sla_alert": {"A.alert", "B.alert"},
    "organization": {"A", "B"},
    "app_user": {"A.client", "B.client", "agent", "admin"},
    **CATALOGS,
    "sla_policy": {"default_policy", "A.policy", "B.policy"},
}


def client_view(org):
    return {
        "ticket": {f"{org}.ticket"},
        "ticket_comment": {f"{org}.comment"},  # Never internal notes.
        "ticket_event": {f"{org}.event"},
        "sla_alert": set(),  # Staff only.
        "organization": {org},
        "app_user": {f"{org}.client", "agent", "admin"},  # Colleagues and staff.
        **CATALOGS,
        "sla_policy": {"default_policy", f"{org}.policy"},
    }


EXPECTED = {
    "no_context": NOTHING,
    "login": {**NOTHING, "app_user": {"A.client"}},
    "identify": {**NOTHING, "app_user": {"A.client"}},
    "client_a": client_view("A"),
    "client_b": client_view("B"),
    "agent": STAFF,
    "admin": STAFF,
    "system": STAFF,
}


@pytest.fixture
def dataset():
    """Create the rows (system context) and return {table: {id: label}}."""
    labels = {table: {} for table in TABLES}

    def add(table, label, row):
        labels[table][row.id] = label
        return row

    calendar = add("business_calendar", "calendar", BusinessCalendarFactory())
    add("business_hours", "hours", BusinessHoursFactory(calendar=calendar))
    add("holiday", "holiday", HolidayFactory(calendar=calendar))
    category = add("category", "category", CategoryFactory())
    # Tickets use the default policy of their priority.
    add("sla_policy", "default_policy", default_policy(Priority.MEDIUM))
    agent = add("app_user", "agent", AgentUserFactory())
    admin = add("app_user", "admin", AdminUserFactory())

    users = {}
    for org in ("A", "B"):
        organization = add("organization", org, OrganizationFactory(business_calendar=calendar))
        client = add("app_user", f"{org}.client", ClientUserFactory(organization=organization))
        users[org] = client
        add(
            "sla_policy",
            f"{org}.policy",
            SlaPolicyFactory(organization=organization, priority=Priority.HIGH),
        )
        ticket = add(
            "ticket",
            f"{org}.ticket",
            TicketFactory(
                organization=organization,
                requester=client,
                category=category,
                title=f"{org} title",
            ),
        )
        add("ticket_comment", f"{org}.comment", TicketCommentFactory(ticket=ticket))
        add(
            "ticket_comment",
            f"{org}.internal_note",
            TicketCommentFactory(ticket=ticket, author=agent, is_internal=True),
        )
        add("ticket_event", f"{org}.event", TicketEventFactory(ticket=ticket))
        add("sla_alert", f"{org}.alert", SlaAlertFactory(ticket=ticket))

    contexts = {
        "no_context": {},
        "login": {"role": "login", "login_email": users["A"].email},
        "identify": {"user_id": users["A"].id},
        "client_a": {
            "role": "client",
            "org_id": users["A"].organization_id,
            "user_id": users["A"].id,
        },
        "client_b": {
            "role": "client",
            "org_id": users["B"].organization_id,
            "user_id": users["B"].id,
        },
        "agent": {"role": "agent", "user_id": agent.id},
        "admin": {"role": "admin", "user_id": admin.id},
        "system": {"role": "system"},
    }
    return labels, contexts


def visible_labels(labels):
    seen = {}
    with connection.cursor() as cursor:
        for table in TABLES:
            # Table names come from TABLES above, never from input.
            cursor.execute(f"SELECT id FROM {table}")
            # A row the dataset did not create would show up as "unexpected".
            seen[table] = {
                labels[table].get(row_id, f"unexpected:{row_id}") for (row_id,) in cursor
            }
    return seen


@pytest.mark.parametrize("context", EXPECTED)
def test_each_context_sees_exactly_its_rows(dataset, context):
    labels, contexts = dataset
    set_context(**contexts[context])

    assert visible_labels(labels) == EXPECTED[context]


def test_a_leaky_function_only_sees_visible_rows(dataset):
    """The classic attack on row-level security, which PostgreSQL blocks.

    The function reports every value it receives. If the planner ran it before
    the policy, the notices would reveal another organization's titles even
    though those rows are not returned. Policies act as a security barrier: their
    conditions run first, unless a function is LEAKPROOF, and only a superuser
    can mark one so. helpdesk_app cannot create functions in public, but it can
    in pg_temp (PUBLIC has TEMPORARY on the database), so the attack is real.
    """
    _, contexts = dataset
    notices = []
    psycopg_connection = connection.connection

    def collect(diagnostic):
        notices.append(diagnostic.message_primary)

    with connection.cursor() as cursor:
        # COST: tell the planner it is cheaper than the policy, so it would run
        # first if the security barrier did not prevent it.
        cursor.execute(
            """
            CREATE FUNCTION pg_temp.leak(value text) RETURNS boolean
                LANGUAGE plpgsql COST 0.0000001
            AS $$ BEGIN RAISE NOTICE 'leak: %', value; RETURN true; END $$
            """
        )
        psycopg_connection.add_notice_handler(collect)
        try:
            # Control: with every row visible the function does report them all,
            # so an empty result below means "blocked", not "not collected".
            set_context(**contexts["system"])
            cursor.execute("SELECT id FROM ticket WHERE pg_temp.leak(title)")
            seen_by_system = sorted(notices)

            notices.clear()
            set_context(**contexts["client_a"])
            cursor.execute("SELECT id FROM ticket WHERE pg_temp.leak(title)")
            seen_by_client = sorted(notices)
        finally:
            psycopg_connection.remove_notice_handler(collect)

    assert seen_by_system == ["leak: A title", "leak: B title"]
    assert seen_by_client == ["leak: A title"]
