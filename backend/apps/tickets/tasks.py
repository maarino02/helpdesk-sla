from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from apps.core.rls import system_context
from apps.tickets.services import sla_alerts

logger = get_task_logger(__name__)


@shared_task
def raise_sla_alerts():
    """Record the SLA alerts that have become due (scheduled every minute).

    Idempotent: a retried or duplicated run inserts nothing new, which is what
    acks_late (at least once delivery) requires.
    """
    with system_context():
        raised = sla_alerts.raise_sla_alerts(timezone.now())
    logger.info("raised %d SLA alerts", len(raised))
    return len(raised)
