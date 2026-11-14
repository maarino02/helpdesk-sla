from celery import shared_task
from celery.utils.log import get_task_logger

# Task loggers include the task name and id in each line.
logger = get_task_logger(__name__)


@shared_task
def ping():
    """Heartbeat that proves the beat -> broker -> worker pipeline works end to end."""
    logger.info("pong")
    return "pong"
