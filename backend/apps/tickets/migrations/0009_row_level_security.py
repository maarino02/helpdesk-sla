"""Row-level security on the tables that hold client data.

Policies decide which rows; the table privileges (core 0001) decide which
operations. Nobody has DELETE on these tables, so one FOR ALL policy per table
is enough. A FOR ALL policy without WITH CHECK uses its USING expression for
both: rows a role cannot see, it cannot write either (a client cannot create a
ticket in, or move one to, another organization).

FORCE ROW LEVEL SECURITY: the owner is subject to the policies too, so a data
migration must set app.role = 'system' to see any row.

Foreign key checks bypass row-level security. A client could reference another
organization's ticket_id in a comment, but the policy forces the comment's
organization_id to be the client's own, and the composite foreign key
(ticket_id, organization_id) forces the ticket to belong to that organization.
"""

from django.db import migrations

# Staff and background jobs see every organization; clients only their own.
# With no context app_role() and app_org_id() are NULL and nothing matches.
TENANT_ACCESS = (
    "app_role() IN ('agent', 'admin', 'system')"
    " OR (app_role() = 'client' AND organization_id = app_org_id())"
)
STAFF_ONLY = "app_role() IN ('agent', 'admin', 'system')"

TABLES = ("ticket", "ticket_comment", "ticket_event", "sla_alert")


def _enable(table):
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;\n"
    )


POLICIES = (
    "".join(_enable(table) for table in TABLES)
    + f"""
CREATE POLICY tenant_access ON ticket USING ({TENANT_ACCESS});
CREATE POLICY tenant_access ON ticket_comment USING ({TENANT_ACCESS});
CREATE POLICY tenant_access ON ticket_event USING ({TENANT_ACCESS});

-- Restrictive policies are combined with AND with the permissive ones, so this
-- rule holds whatever tenant_access says. IS DISTINCT FROM: a NULL role counts
-- as "not a client" here; tenant_access already hides everything from it.
CREATE POLICY hide_internal_notes_from_clients ON ticket_comment
    AS RESTRICTIVE
    USING (app_role() IS DISTINCT FROM 'client' OR NOT is_internal);

CREATE POLICY staff_only ON sla_alert USING ({STAFF_ONLY});
"""
)

DROP_POLICIES = """
DROP POLICY staff_only ON sla_alert;
DROP POLICY hide_internal_notes_from_clients ON ticket_comment;
DROP POLICY tenant_access ON ticket_event;
DROP POLICY tenant_access ON ticket_comment;
DROP POLICY tenant_access ON ticket;
""" + "".join(
    f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;\n"
    f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;\n"
    for table in TABLES
)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_rls_context_functions"),
        ("tickets", "0008_sla_alert_composite_fk"),
    ]

    operations = [
        migrations.RunSQL(sql=POLICIES, reverse_sql=DROP_POLICIES),
    ]
