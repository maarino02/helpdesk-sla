"""Row-level security on organization.

Staff and system see every organization; a client only their own. Creating a
ticket increments next_ticket_number (SELECT ... FOR UPDATE), so clients and
agents need UPDATE on the row. The trigger limits them to that column: they
cannot rename or deactivate the organization. Only admin and system create
organizations; nobody deletes them (no DELETE privilege).
"""

from django.db import migrations

ACCESS = (
    "app_role() IN ('agent', 'admin', 'system') OR (app_role() = 'client' AND id = app_org_id())"
)

POLICIES = f"""
ALTER TABLE organization ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_select ON organization FOR SELECT USING ({ACCESS});
CREATE POLICY tenant_update ON organization FOR UPDATE USING ({ACCESS});
CREATE POLICY organization_admin ON organization FOR INSERT
    WITH CHECK (app_role() IN ('admin', 'system'));

CREATE TRIGGER organization_restrict_update
    BEFORE UPDATE ON organization
    FOR EACH ROW
    EXECUTE FUNCTION app_restrict_update('next_ticket_number');
"""

DROP_POLICIES = """
DROP TRIGGER organization_restrict_update ON organization;
DROP POLICY organization_admin ON organization;
DROP POLICY tenant_update ON organization;
DROP POLICY tenant_select ON organization;
ALTER TABLE organization NO FORCE ROW LEVEL SECURITY;
ALTER TABLE organization DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_rls_login_and_column_guard"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=POLICIES, reverse_sql=DROP_POLICIES),
    ]
