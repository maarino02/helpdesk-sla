# Hand-written: Django does not support composite foreign keys.

from django.db import migrations

# The denormalized organization_id of comments and events must be the ticket's.
# The FK references the ticket_unique_id_organization constraint (tickets 0003).
# Same choices as ticket_requester_same_organization_fk (tickets 0004): not
# DEFERRABLE, so the INSERT itself fails; ON UPDATE / ON DELETE NO ACTION, so a ticket
# with comments or events can be neither moved to another organization nor deleted.
COMPOSITE_FKS = [
    ("ticket_comment", "ticket_comment_ticket_same_organization_fk"),
    ("ticket_event", "ticket_event_ticket_same_organization_fk"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0005_ticket_comment_and_event"),
    ]

    operations = [
        migrations.RunSQL(
            sql=f"""
                ALTER TABLE {table}
                ADD CONSTRAINT {name}
                FOREIGN KEY (ticket_id, organization_id)
                REFERENCES ticket (id, organization_id);
            """,
            reverse_sql=f"ALTER TABLE {table} DROP CONSTRAINT {name};",
        )
        for table, name in COMPOSITE_FKS
    ]
