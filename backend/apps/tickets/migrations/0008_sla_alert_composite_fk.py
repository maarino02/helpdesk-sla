# Hand-written: Django does not support composite foreign keys.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0007_sla_alert"),
    ]

    operations = [
        # Same as tickets 0006 for comments and events: the denormalized
        # organization_id must be the ticket's. Not DEFERRABLE; ON UPDATE / ON DELETE
        # NO ACTION.
        migrations.RunSQL(
            sql="""
                ALTER TABLE sla_alert
                ADD CONSTRAINT sla_alert_ticket_same_organization_fk
                FOREIGN KEY (ticket_id, organization_id)
                REFERENCES ticket (id, organization_id);
            """,
            reverse_sql="ALTER TABLE sla_alert DROP CONSTRAINT sla_alert_ticket_same_organization_fk;",
        ),
    ]
