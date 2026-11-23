from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0014_ticket_warning_instants"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ticket",
            name="first_response_warning_at",
            field=models.DateTimeField(),
        ),
        migrations.AlterField(
            model_name="ticket",
            name="resolution_warning_at",
            field=models.DateTimeField(),
        ),
        migrations.AddIndex(
            model_name="ticket",
            index=models.Index(
                condition=models.Q(("status__in", ["new", "open"])),
                fields=["resolution_warning_at"],
                name="ticket_resolution_warn_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="ticket",
            index=models.Index(
                condition=models.Q(("first_response_at__isnull", True)),
                fields=["first_response_warning_at"],
                name="ticket_first_resp_warn_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="ticket",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("first_response_warning_at__lte", models.F("first_response_due_at"))
                ),
                name="ticket_first_resp_warn_before_due",
            ),
        ),
        migrations.AddConstraint(
            model_name="ticket",
            constraint=models.CheckConstraint(
                condition=models.Q(("resolution_warning_at__lte", models.F("resolution_due_at"))),
                name="ticket_resolution_warn_before_due",
            ),
        ),
    ]
