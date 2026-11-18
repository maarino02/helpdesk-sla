"""Row-level security on the SLA catalogs.

Two permissive policies per table, combined with OR for each command:
catalog_read lets every known role read, and catalog_admin (FOR ALL) is the
only one that applies to INSERT, UPDATE and DELETE. Without context, or with the
login context, nothing is visible.

sla_policy is not a plain catalog: a row with organization_id is a client's own
agreement, which other clients must not see.
"""

from django.db import migrations

READERS = "app_role() IN ('client', 'agent', 'admin', 'system')"
ADMINS = "app_role() IN ('admin', 'system')"
SLA_POLICY_READERS = (
    "app_role() IN ('agent', 'admin', 'system')"
    " OR (app_role() = 'client'"
    " AND (organization_id IS NULL OR organization_id = app_org_id()))"
)

TABLES = {
    "business_calendar": READERS,
    "business_hours": READERS,
    "holiday": READERS,
    "sla_policy": SLA_POLICY_READERS,
}

POLICIES = "".join(
    f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY catalog_read ON {table} FOR SELECT USING ({readers});
CREATE POLICY catalog_admin ON {table} USING ({ADMINS});
"""
    for table, readers in TABLES.items()
)

DROP_POLICIES = "".join(
    f"""
DROP POLICY catalog_admin ON {table};
DROP POLICY catalog_read ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""
    for table in TABLES
)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_rls_login_and_column_guard"),
        ("sla", "0003_sla_policy"),
    ]

    operations = [
        migrations.RunSQL(sql=POLICIES, reverse_sql=DROP_POLICIES),
    ]
