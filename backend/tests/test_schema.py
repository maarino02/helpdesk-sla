"""Schema drift tests: the real PostgreSQL catalog must match what the code declares.

Django only knows the constraints declared in the models. The composite foreign keys
are created with RunSQL, and the Django FKs they replace use db_constraint=False, so
if one of those migrations were removed or broken, nothing in Django would notice.
These tests compare pg_constraint with the models in both directions:

- every constraint in the database is declared in a model or listed here, and
- everything declared or listed here exists in the database.
"""

import pytest
from django.apps import apps
from django.db import connection, models

pytestmark = pytest.mark.django_db

PROJECT_APPS = ("accounts", "organizations", "sla", "tickets")

# The only constraints created with RunSQL. A new one must be added here and
# documented in docs/data-model.md.
# name -> (table, columns, referenced table, referenced columns)
COMPOSITE_FOREIGN_KEYS = {
    "ticket_requester_same_organization_fk": (
        "ticket",
        ("requester_id", "organization_id"),
        "app_user",
        ("id", "organization_id"),
    ),
    "ticket_comment_ticket_same_organization_fk": (
        "ticket_comment",
        ("ticket_id", "organization_id"),
        "ticket",
        ("id", "organization_id"),
    ),
    "ticket_event_ticket_same_organization_fk": (
        "ticket_event",
        ("ticket_id", "organization_id"),
        "ticket",
        ("id", "organization_id"),
    ),
    "sla_alert_ticket_same_organization_fk": (
        "sla_alert",
        ("ticket_id", "organization_id"),
        "ticket",
        ("id", "organization_id"),
    ),
}


def project_models():
    # Auto-created many-to-many tables (e.g. app_user_groups) are not included.
    return [m for app in PROJECT_APPS for m in apps.get_app_config(app).get_models()]


def project_tables():
    return [m._meta.db_table for m in project_models()]


def is_index_backed(constraint):
    # A UniqueConstraint on expressions or with a condition is created as a unique
    # index, so it appears in pg_indexes but not in pg_constraint.
    return isinstance(constraint, models.UniqueConstraint) and bool(
        constraint.expressions or constraint.condition
    )


def database_foreign_keys():
    """name -> (table, columns, referenced table, referenced columns)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.conname,
                   c.conrelid::regclass::text,
                   ARRAY(
                       SELECT a.attname::text
                       FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, position)
                       JOIN pg_attribute a
                         ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                       ORDER BY k.position
                   ),
                   c.confrelid::regclass::text,
                   ARRAY(
                       SELECT a.attname::text
                       FROM unnest(c.confkey) WITH ORDINALITY AS k(attnum, position)
                       JOIN pg_attribute a
                         ON a.attrelid = c.confrelid AND a.attnum = k.attnum
                       ORDER BY k.position
                   )
            FROM pg_constraint c
            WHERE c.contype = 'f' AND c.conrelid::regclass::text = ANY(%s)
            """,
            [project_tables()],
        )
        return {
            name: (table, tuple(columns), ref_table, tuple(ref_columns))
            for name, table, columns, ref_table, ref_columns in cursor.fetchall()
        }


def django_foreign_keys():
    """(table, columns, referenced table, referenced columns) of the FKs Django creates."""
    return {
        (
            model._meta.db_table,
            (field.column,),
            field.related_model._meta.db_table,
            (field.target_field.column,),
        )
        for model in project_models()
        for field in model._meta.local_fields
        if field.many_to_one and field.db_constraint
    }


def test_composite_foreign_keys_match_the_list():
    composite = {
        name: definition
        for name, definition in database_foreign_keys().items()
        if len(definition[1]) > 1
    }

    assert composite == COMPOSITE_FOREIGN_KEYS


def test_simple_foreign_keys_match_the_models():
    # Compared by structure, not by name: Django names FKs with a hash. A missing FK
    # here would mean a field with db_constraint=False and no composite FK behind it.
    simple = {
        definition for definition in database_foreign_keys().values() if len(definition[1]) == 1
    }

    assert simple == django_foreign_keys()


def test_check_unique_and_exclusion_constraints_match_the_models():
    # The project declares every one of these as a named Meta constraint (never
    # unique=True or unique_together), so the names can be compared directly.
    declared = {
        constraint.name
        for model in project_models()
        for constraint in model._meta.constraints
        if not is_index_backed(constraint)
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE contype IN ('c', 'u', 'x') AND conrelid::regclass::text = ANY(%s)",
            [project_tables()],
        )
        in_database = {row[0] for row in cursor.fetchall()}

    assert in_database == declared


def test_declared_indexes_exist():
    declared = {index.name for model in project_models() for index in model._meta.indexes} | {
        constraint.name
        for model in project_models()
        for constraint in model._meta.constraints
        if is_index_backed(constraint)
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = ANY(%s)",
            [project_tables()],
        )
        in_database = {row[0] for row in cursor.fetchall()}

    assert declared <= in_database
