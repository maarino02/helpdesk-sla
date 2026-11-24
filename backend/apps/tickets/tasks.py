from smtplib import SMTPException

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.utils import timezone

from apps.core.rls import system_context
from apps.tickets.services import auto_close, notifications, sla_alerts

logger = get_task_logger(__name__)


@shared_task
def raise_sla_alerts():
    """Record the SLA alerts that have become due (scheduled every minute).

    Idempotent: a retried or duplicated run inserts nothing new, which is what
    acks_late (at least once delivery) requires. RETURNING yields only the new
    alerts, so each one is notified once, after the commit.
    """
    with system_context():
        raised = sla_alerts.raise_sla_alerts(timezone.now())
        for alert in raised:
            notifications.notify_on_commit(notifications.SLA_ALERT, alert.id)
    logger.info("raised %d SLA alerts", len(raised))
    return len(raised)


@shared_task
def notify(kind, source_id):
    """Fan out a notification: one send_notification task per recipient.

    Recipients are decided here, after the commit, with current data (e.g. an
    assignee deactivated in the meantime). No SMTP here, so nothing to retry.
    """
    with system_context():
        recipients = notifications.recipient_ids(kind, source_id)
    for recipient_id in recipients:
        send_notification.delay(kind, source_id, recipient_id)
    return len(recipients)


@shared_task(
    autoretry_for=(SMTPException, OSError),
    retry_backoff=True,  # 1 s, 2 s, 4 s... with jitter
    max_retries=5,
)
def send_notification(kind, source_id, recipient_id):
    """Render and send one email. Retried on SMTP and network errors only."""
    with system_context():
        message = notifications.build_message(kind, source_id, recipient_id)
    # Sent after the transaction: no database connection held while SMTP answers.
    message.send()


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
