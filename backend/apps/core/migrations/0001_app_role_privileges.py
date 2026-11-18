"""Table privileges of the application role (helpdesk_app).

Deny by default: the role starts with nothing on every table and gets only what
the application needs. TRUNCATE, REFERENCES and TRIGGER are never granted.
Rows are not deleted from tickets, organizations or users (they are closed or
deactivated), and comments, events and alerts are append-only history.

Primary keys are identity columns, so INSERT on the table is enough: the
implicit sequences need no separate grant.

Every later migration that creates a table must grant its privileges too;
backend/tests/test_privileges.py fails for any table without a decision.
"""

from django.db import migrations

GRANTS = """
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM helpdesk_app;

GRANT SELECT, INSERT, UPDATE
    ON ticket, organization, app_user
    TO helpdesk_app;

GRANT SELECT, INSERT
    ON ticket_comment, ticket_event, sla_alert, django_admin_log
    TO helpdesk_app;

-- Catalogs: row-level security will limit writes to admins. Sessions are
-- deleted when they expire. The Django group and permission tables back the
-- user admin form.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON category, business_calendar, business_hours, holiday, sla_policy,
       django_session,
       auth_group, auth_group_permissions, app_user_groups, app_user_user_permissions
    TO helpdesk_app;

-- Rows written only by migrate (as the owner). makemigrations reads
-- django_migrations on every connection to check the migration history.
GRANT SELECT
    ON django_content_type, auth_permission, django_migrations
    TO helpdesk_app;
"""

REVOKE_ALL = "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM helpdesk_app;"


class Migration(migrations.Migration):
    # The last migration of every app that owns one of these tables, named
    # explicitly. "__latest__" would be resolved again on every run: once an app
    # gets a new migration, this one (already applied) would depend on an
    # unapplied one and Django would refuse to migrate.
    dependencies = [
        ("accounts", "0003_user_unique_id_organization"),
        ("admin", "0003_logentry_add_action_flag_choices"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("organizations", "0001_initial"),
        ("sessions", "0001_initial"),
        ("sla", "0003_sla_policy"),
        ("tickets", "0008_sla_alert_composite_fk"),
    ]

    operations = [
        migrations.RunSQL(sql=GRANTS, reverse_sql=REVOKE_ALL),
    ]
