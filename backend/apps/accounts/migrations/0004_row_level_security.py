"""Row-level security on app_user.

Permissive policies are combined with OR for each command: SELECT is allowed by
any of the four, INSERT only by user_admin.

Requests identify and then widen the context (ADR-0004):
1. Login: app.role = 'login' and app.login_email read that one row, read-only.
   The database cannot know whether the password was checked, so nothing is
   written in this context.
2. Identify: app.user_id alone reads the user's own row, from which the current
   role and organization are taken.
3. Widen: app.role and app.org_id grant what the role allows.

Clients see the staff rows (to display the assignee). Row-level security works
on rows, not columns: the application must never serialize the password hash.

own_row_update lets every user update their own row (last_login, password), and
the trigger limits roles other than admin and system to those columns: an agent
cannot make themselves superuser, a client cannot change role or organization.
"""

from django.db import migrations

STAFF = "app_role() IN ('agent', 'admin', 'system')"

POLICIES = f"""
ALTER TABLE app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user FORCE ROW LEVEL SECURITY;

CREATE POLICY own_row_select ON app_user FOR SELECT
    USING (id = app_user_id());

-- Without WITH CHECK the USING expression is used: the row stays the user's own.
CREATE POLICY own_row_update ON app_user FOR UPDATE
    USING (id = app_user_id());

CREATE POLICY login_lookup ON app_user FOR SELECT
    USING (app_role() = 'login' AND email = app_login_email());

CREATE POLICY directory ON app_user FOR SELECT
    USING (
        {STAFF}
        OR (app_role() = 'client'
            AND (organization_id = app_org_id() OR role <> 'client'))
    );

CREATE POLICY user_admin ON app_user
    USING (app_role() IN ('admin', 'system'));

CREATE TRIGGER app_user_restrict_update
    BEFORE UPDATE ON app_user
    FOR EACH ROW
    EXECUTE FUNCTION app_restrict_update('password', 'last_login', 'full_name');
"""

DROP_POLICIES = """
DROP TRIGGER app_user_restrict_update ON app_user;
DROP POLICY user_admin ON app_user;
DROP POLICY directory ON app_user;
DROP POLICY login_lookup ON app_user;
DROP POLICY own_row_update ON app_user;
DROP POLICY own_row_select ON app_user;
ALTER TABLE app_user NO FORCE ROW LEVEL SECURITY;
ALTER TABLE app_user DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_user_unique_id_organization"),
        ("core", "0003_rls_login_and_column_guard"),
    ]

    operations = [
        migrations.RunSQL(sql=POLICIES, reverse_sql=DROP_POLICIES),
    ]
