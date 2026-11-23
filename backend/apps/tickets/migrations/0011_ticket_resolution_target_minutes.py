"""Add ticket.resolution_target_minutes and fill it for existing tickets.

Nullable here; 0012 makes it NOT NULL and adds the CHECKs once the data is
consistent. Two migrations so that each one does one thing: a data UPDATE
followed by ALTER TABLE on the same table in one transaction can also fail with
"pending trigger events" when deferred constraints are involved.

Row-level security is forced, so the owner running the migration sees no rows
without a context: the UPDATEs run with app.role = 'system', local to the
migration's transaction.
"""

from django.db import migrations, models

FILL = """
SELECT set_config('app.role', 'system', true);

-- The best available value: the policy's current duration.
UPDATE ticket
SET resolution_target_minutes = sla_policy.resolution_minutes
FROM sla_policy
WHERE sla_policy.id = ticket.sla_policy_id;

-- The pause starts when the ticket entered pending or resolved. That instant is
-- not stored for pending tickets, so the last update is the closest known one.
UPDATE ticket
SET sla_paused_at = coalesce(resolved_at, updated_at)
WHERE status IN ('pending', 'resolved') AND sla_paused_at IS NULL;

UPDATE ticket SET sla_paused_at = NULL
WHERE status NOT IN ('pending', 'resolved') AND sla_paused_at IS NOT NULL;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0010_category_row_level_security"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="resolution_target_minutes",
            field=models.IntegerField(null=True),
        ),
        # Nothing to undo: the reverse migration drops the column.
        migrations.RunSQL(sql=FILL, reverse_sql=migrations.RunSQL.noop),
    ]
