-- Workload and output per staff member (docs/analytics.md#agent-workload).
--
-- Two kinds of figures side by side:
-- - the open work, a snapshot at now: tickets new, open or pending assigned to each
--   person, and how many have already breached their resolution deadline (the
--   predicate of open_ticket_aging.sql);
-- - the output of the period: tickets resolved, their median business minutes, and
--   how many were resolved late (the predicate of sla_compliance.sql).
--
-- A ticket belongs to its current assignee, for the open work and for resolutions
-- alike, like its resolved_at and resolution_business_minutes. Who clicked
-- "resolve" is in ticket_event, but a reopened ticket may have several resolvers
-- and its minutes cannot be split between them.
--
-- Rows: every active agent and admin (both can be assigned), with zeros if they
-- have nothing; anyone else who still holds tickets (deactivated since, for
-- instance), so that no ticket is left out; and one 'unassigned' row (agent_id
-- NULL), always last. The rows add up to the totals of the other metrics.
--
-- Each kind of figure is aggregated first, one row per assignee, and only then
-- joined to the users. Joining the users to the tickets twice and counting
-- afterwards would multiply rows (3 open x 4 resolved = 12 per person) and
-- inflate every count.
--
-- Parameters: start, end (timestamptz, end exclusive), org (bigint or NULL for
-- every client), now (timestamptz).

WITH open_work AS (
    SELECT assignee_id,
           count(*) AS open_tickets,
           count(*) FILTER (WHERE status = 'pending') AS pending,
           count(*) FILTER (
               WHERE resolution_due_at <= coalesce(sla_paused_at, %(now)s)
           ) AS open_breached
    FROM ticket
    WHERE status IN ('new', 'open', 'pending')
      -- The client filter goes here, inside each aggregate, and never in the
      -- final WHERE: there it would drop the people without tickets for that client.
      AND (%(org)s::bigint IS NULL OR organization_id = %(org)s)
    GROUP BY assignee_id
),

resolved AS (
    SELECT assignee_id,
           count(*) AS resolved,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_business_minutes)
               AS resolution_median,
           count(*) FILTER (WHERE resolved_at >= resolution_due_at) AS resolved_late
    FROM ticket
    WHERE resolved_at >= %(start)s
      AND resolved_at < %(end)s
      AND (%(org)s::bigint IS NULL OR organization_id = %(org)s)
    GROUP BY assignee_id
),

people AS (
    SELECT id, full_name, role, is_active
    FROM app_user
    UNION ALL
    -- The unassigned tickets: the NULL key matches them below.
    SELECT NULL, NULL, NULL, NULL
)

SELECT p.id AS agent_id,
       p.full_name,
       p.role,
       p.is_active,
       coalesce(o.open_tickets, 0) AS open_tickets,
       coalesce(o.pending, 0) AS pending,
       coalesce(o.open_breached, 0) AS open_breached,
       coalesce(r.resolved, 0) AS resolved,
       round(r.resolution_median::numeric, 1) AS resolution_median,
       coalesce(r.resolved_late, 0) AS resolved_late
FROM people AS p
-- IS NOT DISTINCT FROM: = with NULL = NULL true, for the unassigned row. It rules
-- out a hash join, which is fine here: both sides have one row per person.
LEFT JOIN open_work AS o ON o.assignee_id IS NOT DISTINCT FROM p.id
LEFT JOIN resolved AS r ON r.assignee_id IS NOT DISTINCT FROM p.id
-- Which rows, not which tickets: active staff always, anyone else only if a join
-- found work for them. The unassigned row always.
WHERE p.id IS NULL
   OR (p.role IN ('agent', 'admin') AND p.is_active)
   OR o.assignee_id IS NOT NULL
   OR r.assignee_id IS NOT NULL
ORDER BY p.id IS NULL, open_tickets DESC, p.full_name, p.id
