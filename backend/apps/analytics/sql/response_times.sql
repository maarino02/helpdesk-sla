-- First response and resolution times per month and priority
-- (docs/analytics.md#response-times).
--
-- The main figures are business minutes, the time the SLA counts (ADR-0002):
-- first_response_business_minutes from creation to the first public staff reply,
-- resolution_business_minutes without the pauses (waiting for the client, or
-- resolved until reopened). The wall-clock median, from creation, is secondary:
-- what the client lived, nights and weekends included.
--
-- As in sla_compliance.sql, each outcome counts in the month it happened, in the
-- provider's time zone, and the grid of months x priorities ('all' included)
-- comes back complete: counts 0 and NULL times where there is nothing to measure.
--
-- Medians and the 90th percentile with percentile_cont, which interpolates
-- between the two middle values (the median of 10 and 20 minutes is 15), rounded
-- to one decimal. The p90 says how long the slowest tenth took: an average would
-- hide them behind the many fast tickets.
--
-- Parameters: start, end (timestamptz, end exclusive), org (bigint or NULL for
-- every client), tz (IANA name), priorities (text[], in display order).

WITH months AS (
    SELECT generate_series(
        date_trunc('month', %(start)s::timestamptz AT TIME ZONE %(tz)s),
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

first_responses AS (
    SELECT date_trunc('month', first_response_at AT TIME ZONE %(tz)s)::date AS month,
           CASE WHEN GROUPING(priority) = 1 THEN 'all' ELSE priority END AS priority,
           count(*) AS total,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY first_response_business_minutes) AS median,
           percentile_cont(0.9) WITHIN GROUP (ORDER BY first_response_business_minutes) AS p90,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY extract(epoch FROM first_response_at - created_at) / 60
           ) AS median_wall
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
           percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_business_minutes) AS median,
           percentile_cont(0.9) WITHIN GROUP (ORDER BY resolution_business_minutes) AS p90,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY extract(epoch FROM resolved_at - created_at) / 60
           ) AS median_wall
    FROM ticket
    WHERE resolved_at >= %(start)s
      AND resolved_at < %(end)s
      AND (%(org)s::bigint IS NULL OR organization_id = %(org)s)
    GROUP BY GROUPING SETS ((1, priority), (1))
)

-- percentile_cont returns double precision; round() needs numeric.
SELECT m.month,
       p.priority,
       coalesce(f.total, 0) AS first_responses,
       round(f.median::numeric, 1) AS first_response_median,
       round(f.p90::numeric, 1) AS first_response_p90,
       round(f.median_wall::numeric, 1) AS first_response_median_wall,
       coalesce(r.total, 0) AS resolutions,
       round(r.median::numeric, 1) AS resolution_median,
       round(r.p90::numeric, 1) AS resolution_p90,
       round(r.median_wall::numeric, 1) AS resolution_median_wall
FROM months AS m
CROSS JOIN priorities AS p
LEFT JOIN first_responses AS f ON f.month = m.month AND f.priority = p.priority
LEFT JOIN resolutions AS r ON r.month = m.month AND r.priority = p.priority
ORDER BY m.month, p.position
