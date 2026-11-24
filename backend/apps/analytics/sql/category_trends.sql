-- Tickets created per month and category, with the change from the previous month,
-- the category's share of the month and its rank (docs/analytics.md#category-trends).
--
-- Demand coming in: each ticket counts in the month it was created, in the
-- provider's time zone, under its current category (the one triage settled on,
-- not necessarily the one the client picked).
--
-- Window functions, and the order they need:
-- - lag() reads the previous ROW of the partition, not the previous month. A
--   category with no tickets in March has no March row in a plain GROUP BY, and
--   April would be compared with February. So the grid months x categories is
--   built first and the counts joined to it: every month has a row, zeros included.
-- - The grid starts one month before the period, so the first month is compared
--   with a real figure instead of NULL. That extra month is dropped at the end, in
--   the outer query: window functions run after WHERE, so filtering it out in the
--   same query would hide it from lag() too.
-- - rank(): ties share a position and the next one is skipped (1, 1, 3).
--
-- Months are whole months from the first one (a start on the 15th still counts
-- from the 1st, so every month compares like with like); the last month ends at
-- end, the current month so far.
--
-- Rows: every active category, and inactive ones with tickets in the grid, so the
-- rows add up to the month's total.
--
-- Parameters: start, end (timestamptz, end exclusive), org (bigint or NULL for
-- every client), tz (IANA name).

WITH bounds AS (
    SELECT date_trunc('month', %(start)s::timestamptz AT TIME ZONE %(tz)s)::date AS first_month,
           date_trunc('month', %(start)s::timestamptz AT TIME ZONE %(tz)s)
               - interval '1 month' AS before_first,
           date_trunc('month', (%(end)s::timestamptz - interval '1 microsecond') AT TIME ZONE %(tz)s)
               AS last_month
),

months AS (
    SELECT generate_series(before_first, last_month, interval '1 month')::date AS month
    FROM bounds
),

created AS (
    SELECT date_trunc('month', created_at AT TIME ZONE %(tz)s)::date AS month,
           category_id,
           count(*) AS tickets
    FROM ticket
    -- Local midnight of the month before, back to timestamptz: a plain range on
    -- created_at. Written out rather than read from bounds: a subquery is only
    -- run at execution, so the planner would guess the range; this expression it
    -- folds to a constant and estimates (47 -> 32 ms for one client, measured).
    WHERE created_at >= (date_trunc('month', %(start)s::timestamptz AT TIME ZONE %(tz)s)
                         - interval '1 month') AT TIME ZONE %(tz)s
      AND created_at < %(end)s
      AND (%(org)s::bigint IS NULL OR organization_id = %(org)s)
    GROUP BY 1, 2
),

grid AS (
    SELECT m.month, c.id AS category_id, c.name, coalesce(t.tickets, 0) AS tickets
    FROM months AS m
    CROSS JOIN category AS c
    LEFT JOIN created AS t ON t.month = m.month AND t.category_id = c.id
    WHERE c.is_active
       OR EXISTS (SELECT 1 FROM created AS x WHERE x.category_id = c.id)
),

trends AS (
    SELECT month,
           category_id,
           name,
           tickets,
           lag(tickets) OVER (PARTITION BY category_id ORDER BY month) AS previous,
           round(100.0 * tickets / nullif(sum(tickets) OVER (PARTITION BY month), 0), 1)
               AS share,
           rank() OVER (PARTITION BY month ORDER BY tickets DESC) AS rank
    FROM grid
)

SELECT month,
       category_id,
       name AS category,
       tickets,
       previous,
       tickets - previous AS change,
       -- NULL from 0: growing from nothing is no percentage.
       round(100.0 * (tickets - previous) / nullif(previous, 0), 1) AS change_pct,
       share,
       rank
FROM trends
WHERE month >= (SELECT first_month FROM bounds)
ORDER BY month, rank, name, category_id
