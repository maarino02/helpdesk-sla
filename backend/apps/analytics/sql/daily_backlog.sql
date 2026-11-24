-- Open tickets at the end of each day, rebuilt from the history
-- (docs/analytics.md#daily-backlog).
--
-- The ticket table only knows the current status, so the past comes from
-- ticket_event. Every event that moves a ticket between statuses adds one to the
-- status it enters and takes one from the status it leaves: 'created' enters new;
-- 'status_changed' leaves from_value and enters to_value. Summed per day and then
-- accumulated in date order, sum() OVER (ORDER BY day), that gives how many
-- tickets were in each status at the end of each day. A change between two open
-- statuses (open -> pending) moves one ticket from one column to the other and
-- leaves the backlog as it was; resolved and closed are not columns at all.
--
-- The starting point, what was open before the first day, is the sum of every
-- earlier event: those are all put in the day before the period, which the
-- running sum starts from and the outer query then drops (as in
-- category_trends.sql, filtered after the window function, which runs after WHERE).
--
-- organization_id is copied onto ticket_event, so the client filter needs no
-- join with ticket.
--
-- Days are those of the provider's time zone, up to today: a day that has not
-- ended yet shows the backlog so far, and future days are not listed.
--
-- Parameters: start, end (timestamptz, end exclusive), org (bigint or NULL for
-- every client), tz (IANA name), now (timestamptz).

WITH bounds AS (
    SELECT (%(start)s::timestamptz AT TIME ZONE %(tz)s)::date AS first_day,
           least(
               (%(end)s::timestamptz - interval '1 microsecond') AT TIME ZONE %(tz)s,
               %(now)s::timestamptz AT TIME ZONE %(tz)s
           )::date AS last_day
),

days AS (
    SELECT generate_series(first_day - 1, last_day, interval '1 day')::date AS day
    FROM bounds
),

daily AS (
    -- Earlier events all land on the day before the period: the starting point.
    SELECT greatest((created_at AT TIME ZONE %(tz)s)::date, (SELECT first_day - 1 FROM bounds))
               AS day,
           count(*) FILTER (WHERE event_type = 'created') AS created,
           count(*) FILTER (WHERE to_value = 'resolved') AS resolved,
           count(*) FILTER (WHERE event_type = 'created')
               - count(*) FILTER (WHERE from_value = 'new') AS new,
           count(*) FILTER (WHERE to_value = 'open')
               - count(*) FILTER (WHERE from_value = 'open') AS open,
           count(*) FILTER (WHERE to_value = 'pending')
               - count(*) FILTER (WHERE from_value = 'pending') AS pending
    FROM ticket_event
    -- Only these two carry statuses: from_value and to_value of the other types
    -- are assignee or category ids and priorities, none of them a status name
    -- today. The filter keeps it so, and skips the events that cannot count.
    WHERE event_type IN ('created', 'status_changed')
      AND created_at < %(end)s
      AND (%(org)s::bigint IS NULL OR organization_id = %(org)s)
    GROUP BY 1
),

running AS (
    -- sum() of bigint returns numeric: cast back, so the JSON has whole numbers.
    SELECT d.day,
           coalesce(x.created, 0) AS created,
           coalesce(x.resolved, 0) AS resolved,
           (sum(coalesce(x.new, 0)) OVER by_day)::bigint AS new,
           (sum(coalesce(x.open, 0)) OVER by_day)::bigint AS open,
           (sum(coalesce(x.pending, 0)) OVER by_day)::bigint AS pending
    FROM days AS d
    LEFT JOIN daily AS x ON x.day = d.day
    WINDOW by_day AS (ORDER BY d.day)
)

SELECT day,
       new + open + pending AS backlog,
       new,
       open,
       pending,
       created,
       resolved
FROM running
WHERE day >= (SELECT first_day FROM bounds)
ORDER BY day
