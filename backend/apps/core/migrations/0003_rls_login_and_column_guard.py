"""Login context and the trigger function that protects columns.

app_login_email(): during login only the email is known. The login backend sets
app.role = 'login' and app.login_email, which lets it read that one user row.

app_restrict_update(allowed columns...): row-level security decides which rows a
role can update, not which columns. Roles other than admin and system may only
change the columns passed as trigger arguments. The rows are compared as jsonb
without those columns, so a column added later is protected without changes
here: the check fails closed.
"""

from django.db import migrations

FUNCTIONS = """
CREATE FUNCTION app_login_email() RETURNS text
    LANGUAGE sql STABLE
    RETURN nullif(current_setting('app.login_email', true), '');

CREATE FUNCTION app_restrict_update() RETURNS trigger
    LANGUAGE plpgsql
AS $$
DECLARE
    -- coalesce: with no trigger arguments every column is protected, instead of
    -- jsonb - NULL giving NULL and the comparison letting everything through.
    allowed text[] := coalesce(TG_ARGV, '{}');
BEGIN
    -- A NULL role (no context) is not in the list, so it falls through to the check.
    IF app_role() IN ('admin', 'system') THEN
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
        RAISE EXCEPTION 'role % may only change % in %',
            coalesce(app_role(), 'none'), array_to_string(allowed, ', '), TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
"""

DROP_FUNCTIONS = """
DROP FUNCTION app_restrict_update();
DROP FUNCTION app_login_email();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_rls_context_functions"),
    ]

    operations = [
        migrations.RunSQL(sql=FUNCTIONS, reverse_sql=DROP_FUNCTIONS),
    ]
