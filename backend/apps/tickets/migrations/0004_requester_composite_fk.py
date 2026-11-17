# Hand-written: Django does not support composite foreign keys.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        # Target unique constraint: app_user (id, organization_id).
        ("accounts", "0003_user_unique_id_organization"),
        # Replaces the simple requester FK dropped in 0003 (db_constraint=False).
        ("tickets", "0003_ticket_unique_id_organization"),
    ]

    operations = [
        # Both columns are NOT NULL in ticket, so with the default MATCH SIMPLE the
        # pair must exist in app_user: the requester is a client of this ticket's
        # organization. Staff users have organization_id NULL, so they never match.
        #
        # Not DEFERRABLE (unlike Django's FKs): the error is raised by the INSERT
        # itself. A deferred FK is only checked at COMMIT, and tests never commit.
        # ON UPDATE / ON DELETE NO ACTION (default): moving a ticket or its requester
        # to another organization is rejected instead of cascading.
        migrations.RunSQL(
            sql="""
                ALTER TABLE ticket
                ADD CONSTRAINT ticket_requester_same_organization_fk
                FOREIGN KEY (requester_id, organization_id)
                REFERENCES app_user (id, organization_id);
            """,
            reverse_sql="""
                ALTER TABLE ticket DROP CONSTRAINT ticket_requester_same_organization_fk;
            """,
        ),
    ]
