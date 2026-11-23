from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0011_ticket_resolution_target_minutes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ticket",
            name="resolution_target_minutes",
            field=models.IntegerField(),
        ),
        migrations.AddConstraint(
            model_name="ticket",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("sla_paused_at__isnull", False),
                        ("status__in", ["pending", "resolved"]),
                    ),
                    models.Q(
                        models.Q(("status__in", ["pending", "resolved"]), _negated=True),
                        ("sla_paused_at__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="ticket_sla_paused_at_matches_status",
            ),
        ),
        migrations.AddConstraint(
            model_name="ticket",
            constraint=models.CheckConstraint(
                condition=models.Q(("resolution_target_minutes__gt", 0)),
                name="ticket_resolution_target_positive",
            ),
        ),
    ]
