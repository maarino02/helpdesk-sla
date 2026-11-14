"""Health check endpoints.

Liveness and readiness answer different questions and are kept apart on purpose:

- live: is the process up and serving requests? A failure means "restart me".
- ready: can it reach its dependencies? A failure means "stop sending me traffic".

If liveness also checked the database, a database outage would make the
orchestrator restart healthy web containers in a loop without fixing anything.

Both endpoints are public and never touch tenant tables, so they work without
authentication or row-level security context.
"""

import logging

import redis
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def live(request):
    return JsonResponse({"status": "ok"})


@require_GET
def ready(request):
    checks = {"database": _check_database(), "redis": _check_redis()}
    healthy = all(result == "ok" for result in checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "unavailable", "checks": checks},
        status=200 if healthy else 503,
    )


def _check_database():
    try:
        # A real round trip: ensure_connection() would not notice a connection
        # that dropped after it was opened.
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        # Full details go to the log only; the public response must not reveal
        # hosts, users or versions.
        logger.exception("Readiness check failed: database unreachable")
        return "error"
    return "ok"


def _check_redis():
    # Short timeouts: without them a network problem would hang the request
    # instead of reporting "unavailable". A client per call is fine for a health
    # check; regular code should share a connection pool.
    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    except redis.RedisError:
        logger.exception("Readiness check failed: Redis unreachable")
        return "error"
    finally:
        client.close()
    return "ok"
