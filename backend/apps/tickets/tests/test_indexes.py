import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


def ticket_indexes():
    """Index name -> definition, as PostgreSQL reports it (not as Django declares it)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'ticket'")
        return dict(cursor.fetchall())


@pytest.mark.parametrize(
    ("name", "definition"),
    [
        ("ticket_org_status_idx", "USING btree (organization_id, status)"),
        ("ticket_assignee_status_idx", "USING btree (assignee_id, status)"),
        (
            "ticket_resolution_due_idx",
            "USING btree (resolution_due_at) WHERE ((status)::text = ANY "
            "((ARRAY['new'::character varying, 'open'::character varying])::text[]))",
        ),
        (
            "ticket_first_resp_due_idx",
            "USING btree (first_response_due_at) WHERE (first_response_at IS NULL)",
        ),
        ("ticket_created_at_idx", "USING btree (created_at)"),
        (
            "ticket_resolved_at_idx",
            "USING btree (resolved_at, id) WHERE ((status)::text = 'resolved'::text)",
        ),
    ],
)
def test_index_exists_with_expected_definition(name, definition):
    assert ticket_indexes()[name].endswith(definition)


def test_no_single_column_index_on_columns_covered_by_composite_ones():
    # organization_id is covered by the unique (organization_id, number) index and
    # assignee_id by (assignee_id, status): a separate index would be redundant.
    redundant = [
        name
        for name, definition in ticket_indexes().items()
        if definition.endswith(("(organization_id)", "(assignee_id)"))
    ]

    assert redundant == []
