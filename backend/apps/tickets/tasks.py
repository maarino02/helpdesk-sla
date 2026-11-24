from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.utils import timezone

from apps.core.rls import system_context
from apps.tickets.services import auto_close, sla_alerts

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


@shared_task
def auto_close_resolved(batch_size=auto_close.BATCH_SIZE):
    """Close the tickets resolved more than AUTO_CLOSE_AFTER ago (hourly).

    One transaction per ticket: a ticket that fails does not undo the others,
    and each row lock is held only for its own close. Every system_context()
    is a durable transaction, so the batch reads and the closes never share one.
    Idempotent: closed tickets are no longer candidates.
    """
    cutoff = timezone.now() - settings.AUTO_CLOSE_AFTER
    closed = 0
    after = None
    while True:
        with system_context():
            batch = auto_close.stale_resolved_batch(cutoff, after=after, limit=batch_size)
        for _resolved_at, ticket_id in batch:
            with system_context():
                closed += auto_close.close_if_stale(ticket_id, cutoff)
        if len(batch) < batch_size:
            break
        after = batch[-1]
    logger.info("auto-closed %d tickets", closed)
    return closed
