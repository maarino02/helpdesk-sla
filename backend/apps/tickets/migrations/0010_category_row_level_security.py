"""Row-level security on category, the same catalog rules as in sla 0004."""

from django.db import migrations

POLICIES = """
ALTER TABLE category ENABLE ROW LEVEL SECURITY;
ALTER TABLE category FORCE ROW LEVEL SECURITY;
CREATE POLICY catalog_read ON category FOR SELECT
    USING (app_role() IN ('client', 'agent', 'admin', 'system'));
CREATE POLICY catalog_admin ON category
    USING (app_role() IN ('admin', 'system'));
"""

DROP_POLICIES = """
DROP POLICY catalog_admin ON category;
DROP POLICY catalog_read ON category;
ALTER TABLE category NO FORCE ROW LEVEL SECURITY;
ALTER TABLE category DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_rls_login_and_column_guard"),
        ("tickets", "0009_row_level_security"),
    ]

    operations = [
        migrations.RunSQL(sql=POLICIES, reverse_sql=DROP_POLICIES),
    ]
