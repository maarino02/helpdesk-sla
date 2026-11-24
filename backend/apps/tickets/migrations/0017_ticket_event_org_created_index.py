from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY does not block inserts into ticket_event while it
    # builds, but it cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("tickets", "0016_ticket_resolved_at_index"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="ticketevent",
            index=models.Index(
                fields=["organization", "created_at"], name="ticket_event_org_created_idx"
            ),
        ),
    ]
