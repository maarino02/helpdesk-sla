-- Age of the open tickets right now, in buckets (docs/analytics.md#open-ticket-aging).
--
-- A snapshot, not a period: the ticket table only knows the current status, so
-- what was open on a past date cannot be read from it.
--
-- Open means new, open or pending: work the support team still has to do. A
-- resolved ticket waits for the client to confirm and is not counted. Age is
-- wall-clock time since creation, what the client has waited.
--
-- Breaches, as the alert job and triage judge them:
-- - resolution: the deadline came before the clock stopped. A pending ticket's
--   clock stopped at sla_paused_at, so it is breached only if it was already
--   late when it was paused (resuming will move the deadline).
-- - first response: not answered yet and the deadline has come.
--
-- Parameters: now (timestamptz), org (bigint or NULL for every client).
-- One row per bucket, empty buckets included, youngest first.

WITH buckets (bucket, position, min_days, max_days) AS (
    VALUES ('under_1_day', 1, 0, 1),
           ('1_to_3_days', 2, 1, 3),
           ('3_to_7_days', 3, 3, 7),
           ('over_7_days', 4, 7, NULL)
),

open_tickets AS (
    SELECT status,
           %(now)s::timestamptz - created_at AS age,
           resolution_due_at <= coalesce(sla_paused_at, %(now)s) AS resolution_breached,
           first_response_at IS NULL AND first_response_due_at <= %(now)s
               AS first_response_overdue
    FROM ticket
    WHERE status IN ('new', 'open', 'pending')
      AND (%(org)s::bigint IS NULL OR organization_id = %(org)s)
)

SELECT b.bucket,
       b.min_days,
       b.max_days,
       count(t.status) AS tickets,
       count(*) FILTER (WHERE t.status = 'new') AS new,
       count(*) FILTER (WHERE t.status = 'open') AS open,
       count(*) FILTER (WHERE t.status = 'pending') AS pending,
       count(*) FILTER (WHERE t.resolution_breached) AS resolution_breached,
       count(*) FILTER (WHERE t.first_response_overdue) AS first_response_overdue
FROM buckets AS b
-- [min, max): a ticket exactly one day old is in 1_to_3_days.
LEFT JOIN open_tickets AS t
       ON t.age >= make_interval(days => b.min_days)
      AND (b.max_days IS NULL OR t.age < make_interval(days => b.max_days))
GROUP BY b.bucket, b.position, b.min_days, b.max_days
ORDER BY b.position
