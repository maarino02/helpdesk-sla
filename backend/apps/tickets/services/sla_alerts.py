"""SLA alert job: record the warning and breach alerts that have become due.

Plain SQL, one INSERT ... SELECT per (kind, level). Each WHERE repeats the
predicate of a partial index on ticket, so the planner can read only the
tickets whose clock is running instead of the whole table.

Two guards against duplicates:
- NOT EXISTS skips the tickets already alerted. Without it, every run would
  try to insert again every ticket past its warning instant and rely on the
  conflict: wasted work that grows with the backlog, and each conflicting row
  still consumes a value of the identity sequence.
- ON CONFLICT DO NOTHING covers two runs at the same time, which NOT EXISTS
  alone does not (both may see "no alert yet" before either commits).

RETURNING yields only the rows actually inserted, so the caller notifies each
alert exactly once.
"""

import datetime
from dataclasses import dataclass

from django.db import connection

_RETURNING = """
ON CONFLICT (ticket_id, kind, level) DO NOTHING
RETURNING id, ticket_id, organization_id, kind, level, due_at
"""

# Resolution clock: runs in new and open (predicate of the partial indexes).
# Warning only while the deadline is still ahead: if the job was down and the
# deadline has passed, the breach alone is raised, not a late warning.
RESOLUTION_WARNING = (
    """
INSERT INTO sla_alert (ticket_id, organization_id, kind, level, due_at)
SELECT t.id, t.organization_id, 'resolution', 'warning', t.resolution_due_at
FROM ticket AS t
WHERE t.status IN ('new', 'open')
  AND t.resolution_warning_at <= %(now)s
  AND t.resolution_due_at > %(now)s
  AND NOT EXISTS (
      SELECT 1 FROM sla_alert AS a
      WHERE a.ticket_id = t.id AND a.kind = 'resolution' AND a.level = 'warning'
  )
"""
    + _RETURNING
)

# A deadline equal to now counts as breached: <= here and > in the warning, so
# every instant falls in exactly one of the two.
RESOLUTION_BREACHED = (
    """
INSERT INTO sla_alert (ticket_id, organization_id, kind, level, due_at)
SELECT t.id, t.organization_id, 'resolution', 'breached', t.resolution_due_at
FROM ticket AS t
WHERE t.status IN ('new', 'open')
  AND t.resolution_due_at <= %(now)s
  AND NOT EXISTS (
      SELECT 1 FROM sla_alert AS a
      WHERE a.ticket_id = t.id AND a.kind = 'resolution' AND a.level = 'breached'
  )
"""
    + _RETURNING
)

# First response clock: never pauses, but stops when the ticket is resolved or
# closed. A ticket resolved without a public reply keeps first_response_at NULL
# forever, so the status filter is needed on top of the index predicate.
FIRST_RESPONSE_WARNING = (
    """
INSERT INTO sla_alert (ticket_id, organization_id, kind, level, due_at)
SELECT t.id, t.organization_id, 'first_response', 'warning', t.first_response_due_at
FROM ticket AS t
WHERE t.first_response_at IS NULL
  AND t.status IN ('new', 'open', 'pending')
  AND t.first_response_warning_at <= %(now)s
  AND t.first_response_due_at > %(now)s
  AND NOT EXISTS (
      SELECT 1 FROM sla_alert AS a
      WHERE a.ticket_id = t.id AND a.kind = 'first_response' AND a.level = 'warning'
  )
"""
    + _RETURNING
)

FIRST_RESPONSE_BREACHED = (
    """
INSERT INTO sla_alert (ticket_id, organization_id, kind, level, due_at)
SELECT t.id, t.organization_id, 'first_response', 'breached', t.first_response_due_at
FROM ticket AS t
WHERE t.first_response_at IS NULL
  AND t.status IN ('new', 'open', 'pending')
  AND t.first_response_due_at <= %(now)s
  AND NOT EXISTS (
      SELECT 1 FROM sla_alert AS a
      WHERE a.ticket_id = t.id AND a.kind = 'first_response' AND a.level = 'breached'
  )
"""
    + _RETURNING
)

STATEMENTS = (
    RESOLUTION_WARNING,
    RESOLUTION_BREACHED,
    FIRST_RESPONSE_WARNING,
    FIRST_RESPONSE_BREACHED,
)


@dataclass(frozen=True)
class RaisedAlert:
    id: int
    ticket_id: int
    organization_id: int
    kind: str
    level: str
    due_at: datetime.datetime


def raise_sla_alerts(now: datetime.datetime) -> list[RaisedAlert]:
    """Insert the alerts due at `now` and return the new ones.

    Runs in the caller's transaction and RLS context: the Celery task uses the
    system context, so every organization is covered and the four statements
    commit together. `now` is a parameter so that the four statements judge the
    same instant and tests can fix it.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    raised = []
    with connection.cursor() as cursor:
        # One execute per statement: psycopg does not accept several statements
        # with parameters in a single call.
        for statement in STATEMENTS:
            cursor.execute(statement, {"now": now})
            raised.extend(RaisedAlert(*row) for row in cursor.fetchall())
    return raised
