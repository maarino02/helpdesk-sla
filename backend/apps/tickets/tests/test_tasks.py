import datetime

import pytest
from django.utils import timezone

from apps.tickets.models import AlertKind, AlertLevel, SlaAlert
from apps.tickets.tasks import raise_sla_alerts
from apps.tickets.tests.factories import TicketFactory
from config.celery import app

pytestmark = pytest.mark.django_db


def test_raise_sla_alerts_task_records_due_alerts_in_the_system_context():
    overdue = timezone.now() - datetime.timedelta(hours=1)
    ticket = TicketFactory(
        resolution_warning_at=overdue,
        resolution_due_at=overdue,
        first_response_at=overdue,
        first_response_business_minutes=10,
    )

    assert raise_sla_alerts() == 1
    alert = SlaAlert.objects.get()
    assert (alert.ticket_id, alert.kind, alert.level) == (
        ticket.pk,
        AlertKind.RESOLUTION,
        AlertLevel.BREACHED,
    )
    assert raise_sla_alerts() == 0


def test_raise_sla_alerts_runs_every_minute_and_drops_stale_runs():
    entry = app.conf.beat_schedule["raise-sla-alerts"]

    assert entry["task"] == raise_sla_alerts.name
    assert entry["schedule"] == 60.0
    assert entry["options"]["expires"] == 60
