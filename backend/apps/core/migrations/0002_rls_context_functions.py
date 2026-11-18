"""Functions that read the row-level security context of the current transaction.

The application sets the context with set_config('app.<name>', value, true),
which lasts until the end of the transaction. These functions return NULL when
the context is missing, and a comparison with NULL is never true, so policies
that use them match no rows: the system fails closed.

- current_setting(name, true): NULL instead of an error if the setting was never
  defined in this session.
- nullif(..., ''): once a setting has been used in a session, PostgreSQL resets
  it to '' (not NULL) at the end of each transaction. Without nullif, the next
  transaction on a reused connection would get '' and the cast to bigint fails.
- STABLE: same result for the whole statement, so the planner evaluates it once
  and can use it in an index condition (organization_id = app_org_id()).
- Not SECURITY DEFINER: they only read session settings and need no privileges.
"""

from django.db import migrations

FUNCTIONS = """
CREATE FUNCTION app_org_id() RETURNS bigint
    LANGUAGE sql STABLE
    RETURN nullif(current_setting('app.org_id', true), '')::bigint;

CREATE FUNCTION app_role() RETURNS text
    LANGUAGE sql STABLE
    RETURN nullif(current_setting('app.role', true), '');

CREATE FUNCTION app_user_id() RETURNS bigint
    LANGUAGE sql STABLE
    RETURN nullif(current_setting('app.user_id', true), '')::bigint;
"""

DROP_FUNCTIONS = """
DROP FUNCTION app_user_id();
DROP FUNCTION app_role();
DROP FUNCTION app_org_id();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_app_role_privileges"),
    ]

    operations = [
        migrations.RunSQL(sql=FUNCTIONS, reverse_sql=DROP_FUNCTIONS),
    ]
