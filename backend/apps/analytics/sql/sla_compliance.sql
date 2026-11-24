-- SLA compliance per month and priority (docs/analytics.md#sla-compliance).
--
-- Each outcome counts in the month it happened, in the provider's time zone:
-- a first response in the month of first_response_at, a resolution in the month
-- of resolved_at (for a reopened ticket, the last resolution). A month's figures
-- are therefore final once it is over: they do not change as old tickets close.
--
-- Met means the instant came before the deadline, as the alert job judges it (a
-- deadline equal to the instant is a breach). Not the business minutes: resolving
-- at 20:00 a ticket due at 18:00 uses no business minutes after 18:00, so
-- resolution_business_minutes equals the target, but the alert job had already
-- raised the breach at 18:00.
--
-- Parameters: start, end (timestamptz, end exclusive), org (bigint or NULL for
-- every client), tz (IANA name), priorities (text[], in display order).
-- One row per month of the period and priority, plus 'all' for the month: months
-- without outcomes come back with zero counts and NULL rates (no data is not zero).

WITH months AS (
    SELECT generate_series(
        date_trunc('month', %(start)s::timestamptz AT TIME ZONE %(tz)s),
        -- The last month that has an instant before the exclusive end.
        date_trunc('month', (%(end)s::timestamptz - interval '1 microsecond') AT TIME ZONE %(tz)s),
        interval '1 month'
    )::date AS month
),

priorities AS (
    SELECT priority, position
    FROM unnest(%(priorities)s::text[]) WITH ORDINALITY AS p(priority, position)
    UNION ALL
    SELECT 'all', cardinality(%(priorities)s::text[]) + 1
),

-- Each outcome aggregated straight from ticket, with the WHERE on its own column:
-- a first response in the period is counted even if the resolution is not, and
-- the other way round. GROUPING SETS gives, in the same pass, the rows per
-- priority and the month's total; GROUPING(priority) = 1 marks the total.
first_responses AS (
    SELECT date_trunc('month', first_response_at AT TIME ZONE %(tz)s)::date AS month,
           CASE WHEN GROUPING(priority) = 1 THEN 'all' ELSE priority END AS priority,
           count(*) AS total,
           count(*) FILTER (WHERE first_response_at < first_response_due_at) AS met
    FROM ticket
    WHERE first_response_at >= %(start)s
      AND first_response_at < %(end)s
      AND (%(org)s::bigint IS NULL OR organization_id = %(org)s)
    GROUP BY GROUPING SETS ((1, priority), (1))
),

resolutions AS (
    SELECT date_trunc('month', resolved_at AT TIME ZONE %(tz)s)::date AS month,
           CASE WHEN GROUPING(priority) = 1 THEN 'all' ELSE priority END AS priority,
           count(*) AS total,
           count(*) FILTER (WHERE resolved_at < resolution_due_at) AS met
    FROM ticket
    WHERE resolved_at >= %(start)s
      AND resolved_at < %(end)s
      AND (%(org)s::bigint IS NULL OR organization_id = %(org)s)
    GROUP BY GROUPING SETS ((1, priority), (1))
)

-- The grid of months x priorities, so that empty cells still come back.
SELECT m.month,
       p.priority,
       coalesce(f.total, 0) AS first_responses,
       coalesce(f.met, 0) AS first_responses_met,
       round(100.0 * f.met / nullif(f.total, 0), 1) AS first_response_rate,
       coalesce(r.total, 0) AS resolutions,
       coalesce(r.met, 0) AS resolutions_met,
       round(100.0 * r.met / nullif(r.total, 0), 1) AS resolution_rate
FROM months AS m
CROSS JOIN priorities AS p
LEFT JOIN first_responses AS f ON f.month = m.month AND f.priority = p.priority
LEFT JOIN resolutions AS r ON r.month = m.month AND r.priority = p.priority
ORDER BY m.month, p.position
