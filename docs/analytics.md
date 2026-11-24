# Analytics

The dashboard metrics are hand-written SQL, one file per query in
`backend/apps/analytics/sql/`. This document explains how they are built and why;
their parameters, permissions and responses are in the
[API reference](api.md#analytics).

| Query | Endpoint | Question it answers |
|---|---|---|
| [SLA compliance](#sla-compliance) | `sla-compliance/` | What share of first responses and resolutions met their deadline, per month and priority? |
| [Response times](#response-times) | `response-times/` | How long do first responses and resolutions take: median and p90, in business minutes? |
| [Open ticket aging](#open-ticket-aging) | `open-ticket-aging/` | How old is the work still to do, and how much of it is already late? |
| [Agent workload](#agent-workload) | `agent-workload/` | What does each person have open now, and what did they resolve in the period? (staff only) |
| [Category trends](#category-trends) | `category-trends/` | Which categories bring the most tickets, and how is that changing month to month? |
| [Daily backlog](#daily-backlog) | `daily-backlog/` | How many tickets were open at the end of each day? |

## How the queries run

**Plain SQL files, a minimal runner.** Each query is a `.sql` file that reads like what
it is and can be pasted into `psql` with the parameters filled in. `queries.py` loads it
once per process and runs it with named parameters (`%(start)s`, psycopg style; a literal
percent sign is written `%%`, in comments too). An ORM version of these queries would be
harder to read and to compare with its `EXPLAIN`, and grids, window functions and
`GROUPING SETS` are where the ORM helps least. The views only translate HTTP: read the
period, run the query, return its rows.

**Same connection, same isolation.** A query runs on the request's connection, inside its
transaction and with the row-level security context the JWT authentication set
([ADR-0004](adr/0004-rls-request-context.md)). PostgreSQL applies the policies to raw SQL
as to any ORM query: a client's query only sees that client's rows even if the SQL forgot
to filter. On top of that every query filters by organization explicitly, with
`(%(org)s::bigint IS NULL OR organization_id = %(org)s)`, for two reasons: defense in
depth, and performance, because the policy is applied as a filter on every row, while the
explicit condition can use an index (see [Performance](#performance)). A client asking
for another organization gets a `403`, not an empty answer that would read as "no data".

**Periods are whole days in one time zone.** The user thinks in days, the data is
`timestamptz`. The period becomes local midnights in the provider's time zone
(`ANALYTICS_TIME_ZONE`, `Europe/Madrid`), with an exclusive end: `>= start` and `<
midnight after the last day`. The same zone for every client, so that the staff
dashboard can add clients up: a ticket answered at 23:30 on 31 March in Mexico City
counts in April for everyone. A client's own time zone is used only to compute its
business hours ([SLA and business time](sla.md)).

## Rules shared by the queries

- **Complete grids.** Months, days, priorities, categories or age buckets are generated
  first (`generate_series`, `VALUES`, `unnest`) and the figures joined to them with
  `LEFT JOIN`. A month without data comes back as a row with zero counts and `null`
  rates or times, never as a missing row: no data is not 0 %, and a missing row breaks
  window functions (see [Category trends](#category-trends)).
- **Each outcome in the month it happened.** A first response counts in the month of
  the reply, a resolution in the month of the (last) resolution, a ticket in the month
  it was created. A finished month's figures never change afterwards, which a "month of
  creation" view would not guarantee for tickets still open.
- **Met means before the deadline**, the instant compared with the stored deadline, as
  the alert job judges it: a reply exactly at the deadline is a breach. Business minutes
  are not compared, because a ticket resolved at 20:00 and due at 18:00 has spent no
  business minutes after 18:00 and is still late.
- **An open ticket is late if its deadline came before its clock stopped:**
  `resolution_due_at <= coalesce(sla_paused_at, now)`. A pending ticket paused before
  its deadline is not late; resuming will move the deadline
  ([ADR-0002](adr/0002-sla-business-time-stored-due-dates.md)).
- **Business minutes are the main figure** (what the SLA counts); wall-clock time is
  secondary (what the client lived, nights and weekends included).
- **One instant per request.** Metrics that look at the present take `now` once in the
  view, pass it to the query and return it as `as_of`, so every row is judged at the same
  moment.
- **The client filter goes inside each aggregate**, never in the final `WHERE` of a
  query built on `LEFT JOIN`: there it would turn the outer join into an inner one and
  drop the rows without data (see [Agent workload](#agent-workload)).

## The queries

### SLA compliance

`sla_compliance.sql`. Months × priorities, plus an `all` row per month.

- First responses and resolutions are two different populations (a ticket answered in
  March may be resolved in April), so each is aggregated in its own CTE and joined to
  the grid.
- `GROUP BY GROUPING SETS ((month, priority), (month))` computes the rows per priority
  and the month's total in one pass; `GROUPING(priority) = 1` marks the total, labelled
  `all`. The total is counted, not added up from the priorities, so it stays right if a
  priority is ever missing from the grid.
- `count(*) FILTER (WHERE first_response_at < first_response_due_at)` counts the met
  ones in the same pass as the total. The rate is `100.0 * met / nullif(total, 0)`:
  `null` without data, instead of a division by zero.
- The last month of the grid is the month of `end - 1 microsecond`: with an exclusive
  end at midnight on 1 May, the last month is April, not May.
- The priorities come from the code (`Priority.values`) as a `text[]` parameter,
  unnested `WITH ORDINALITY` to keep their display order: one list, not one in Python
  and another in the SQL.

### Response times

`response_times.sql`. The same grid, with the median and the 90th percentile of the
first response and resolution times in business minutes, and the wall-clock median.

- `percentile_cont(0.5) WITHIN GROUP (ORDER BY minutes)`: an ordered-set aggregate. It
  interpolates (the median of 10 and 20 is 15); `percentile_disc` would return one of the
  real values instead.
- Median and p90, not the mean: a few very slow tickets move the mean, and the p90 is
  exactly what tells how slow the slowest tenth is.
- The `all` row is the percentile over every ticket of the month: percentiles do not add
  up, so it cannot be computed from the priorities' figures.
- Asking for both percentiles at once (`percentile_cont(ARRAY[0.5, 0.9])`) was measured
  and is not faster: PostgreSQL already shares the sorted input between aggregates with
  the same `ORDER BY`.

### Open ticket aging

`open_ticket_aging.sql`. A snapshot: the ticket table only knows each ticket's current
status, so what was open on a past date cannot be read from it (that is what the
[daily backlog](#daily-backlog) rebuilds from the history). It takes no period, and a
`start` or `end` is rejected with a `400` instead of being silently ignored.

- Open means `new`, `open` or `pending`: work the support team still has to do. A
  resolved ticket is waiting for the client and is not counted.
- Buckets come from a `VALUES` list of `[min_days, max_days)` ranges joined with
  `LEFT JOIN ... ON age >= min AND (max IS NULL OR age < max)`, so empty buckets are
  listed and a ticket exactly one day old falls in exactly one bucket.
- Counts per status with `FILTER`; late resolutions with the shared rule; overdue first
  responses only while still unanswered (a late answer that has happened is counted by
  SLA compliance).

### Agent workload

`agent_workload.sql`. Staff only. Open work now and output over the period, per person.

- **Aggregate, then join.** Open tickets and resolved tickets are two independent
  one-to-many relations of the same user. Joining the users to the tickets twice and
  counting afterwards multiplies the rows: with 3 open and 3 resolved tickets, 9 rows per
  person and every count tripled (a test checks exactly this case). Each relation is
  aggregated first, one row per assignee, and the joins are then one to one.
- **Who gets a row.** Every active agent and admin, with zeros if they have nothing;
  anyone else still holding tickets (deactivated since, for instance), so no ticket is
  left out; and one unassigned row, always last. The rows add up to the totals of the
  other metrics. The final `WHERE` may look at whether a join found something
  (`o.assignee_id IS NOT NULL`): that decides which people are listed, not which tickets
  count.
- **The unassigned row** is joined with `IS NOT DISTINCT FROM`, which treats `NULL` as
  equal to `NULL`. It rules out a hash join, which does not matter here: both sides have
  one row per person.
- **A ticket belongs to its current assignee**, for the open work and for resolutions,
  like its `resolved_at` and `resolution_business_minutes`. Who clicked "resolve" is in
  the history, but a reopened ticket may have several resolvers and its minutes cannot
  be split between them.

### Category trends

`category_trends.sql`. Tickets created per month and category, under the current
category (the one triage settled on), with the change from the month before, the share
of the month and the rank.

- **`lag()` reads the previous row, not the previous month.** In a plain `GROUP BY`, a
  category without tickets in March has no March row, and April would be compared with
  February without anything showing it. The grid months × categories comes first, so
  consecutive rows are consecutive months.
- **The grid starts one month early** so that the first month is compared with a real
  figure instead of `null`. That month is dropped in the outer query, because window
  functions run after `WHERE`: filtering it in the same query would hide it from `lag()`
  too.
- `sum(tickets) OVER (PARTITION BY month)` gives the month's total on every row for the
  share, without a second query or a self-join.
- `rank()`: ties share a position and the next one is skipped (1, 1, 3), as a ranking is
  read. `dense_rank()` (1, 1, 2) would hide that two are ahead; `row_number()` would break
  ties arbitrarily.
- `change_pct` is `null` when the previous month had none: growing from nothing is no
  percentage.
- Months are whole from the first one: a `start` on the 15th still counts its month from
  the 1st, so every month compares like with like.

### Daily backlog

`daily_backlog.sql`. The tickets open at the end of each day of the period, rebuilt
from `ticket_event`, the append-only history.

- **Deltas and a running sum.** Every event that moves a ticket between statuses adds
  one to the status it enters and takes one from the status it leaves (`created` enters
  `new`). Summed per day with `count(*) FILTER` and accumulated with
  `sum() OVER (ORDER BY day)`, that gives how many tickets were in each status at the end
  of each day. A move between two open statuses (`open` → `pending`) leaves the backlog
  unchanged; `resolved` and `closed` are not columns at all.
- **The starting point** is the sum of every event before the period. Those events are
  all put on the day before the first one, which the running sum starts from and the
  outer query then drops (the same pattern as category trends).
- **No join.** `ticket_event` carries `organization_id`, denormalized and protected by
  the composite foreign key `(ticket_id, organization_id)`
  ([data model](data-model.md)), so the client filter needs no join with `ticket`.
- **The check that it is right:** the figures rebuilt for today must equal the open
  tickets the ticket table holds now. They do, on the demo data and on 200,000 tickets
  (799 = 142 new + 555 open + 102 pending).
- Considered instead: for each ticket, the intervals during which it was open (`lead()`
  over its events), joined with the days by range. Easier to picture, but it sorts every
  event by ticket and joins every interval with every day it covers.

## How the queries are tested

- **Exact results on hand-built data.** Each query has a test module with small datasets
  built to check one rule each: boundaries (a reply exactly at the deadline, a ticket
  exactly one day old, 23:30 UTC on the last day of the month), empty months, ties,
  skewed data where the mean and the median differ, and the client filter.
- **Red tests.** Every rule was broken on purpose (`<` for `<=`, UTC instead of the
  provider's zone, `JOIN` instead of `LEFT JOIN`, a missing client filter, `dense_rank`,
  the filter before the window function, joining before aggregating...) to see a test
  fail. A change that survives is either a missing test, added then, or equivalent
  (for instance `100` against `100.0` where `sum()` already returns `numeric`).
- **Cross-check on the seeded history** (`test_on_demo_data.py`): on realistic data,
  every status, pauses, reopenings, several clients and calendars, each query is compared
  with the same figure computed another way, with the ORM or in Python.
- **Isolation**: an end-to-end test with a real login runs a query without any
  organization filter: a client gets only their rows.
- **Index use**: the daily backlog of one client must use the event index with the client
  as the index condition.

## Performance

Measured on `helpdesk_bench` (43 clients, 200,000 tickets over 24 months, 1.15 million
ticket events) with `make bench-queries`: one year of data, as the application role
with the row-level security context of a real request, best of three runs. Client 4 is
the largest (11 % of the tickets), client 43 the smallest (0.8 %).

| Query | Every client | Client 4 | Client 43 |
|---|---:|---:|---:|
| SLA compliance | 310 ms | 43 ms | 4.6 ms |
| Response times | 455 ms | 58 ms | 6.0 ms |
| Agent workload | 122 ms | 17 ms | 2.7 ms |
| Category trends | 133 ms | 20 ms | 2.7 ms |
| Daily backlog | 796 ms | 126 ms (was 257) | 10.7 ms (was 172) |
| Ticket list, first page | — | 0.1 ms | 2.8 ms |

### What was changed

**`ticket_event (organization_id, created_at)`**, the only index added. The daily
backlog rebuilds the past from the whole history of the client, and without it every
client read all 1.15 million events: 172 → 11 ms for the smallest client, 257 → 126 ms
for the largest. Cost: 31 MB, and one more index entry per event inserted. It is
created with `CREATE INDEX CONCURRENTLY`, which does not block inserts into the busiest
table while it builds.

One query was changed after reading its plan: category trends wrote its lower bound as a
subquery on a CTE, which PostgreSQL only evaluates at execution time, so the planner
guessed the range. Written as an expression of the parameters, it is folded to a
constant and estimated: 47 → 32 ms for one client.

### What was measured and left as it is

- **Every client at once** (staff dashboard): the queries read and group a year of
  tickets or events, 100,000 rows or more. Raising `work_mem` from 4 to 32 MB, so that
  the sorts stay in memory, gains about 5 %: the cost is reading and transforming the
  rows, which no index avoids. If it ever matters, the answer is precomputing, not
  indexing: for the backlog, a daily snapshot written each night by Celery beat, which
  never goes stale because events are only ever inserted.
- **Row-level security**: the policy is evaluated on every row read. On the full event
  scan it adds about 180 ms (585 ms against 400 ms as the superuser). The explicit
  organization filter of each query is what lets PostgreSQL use an index; the policy
  stays a filter.
- **Ticket list of a small client**: an index `(organization_id, created_at, id)` would
  return the first page in 0.09 ms instead of 2.8 ms, but only without row-level
  security. With it, the planner estimates 23 rows where there are 1,678: it cannot
  know the value of `current_setting()` in the policy, so it applies a default
  selectivity, finds sorting 23 rows cheap and never picks the ordered index. An index
  that is never used would only cost writes.
- **Alert job**: its four statements take under 2 ms each and use the partial indexes
  on the deadlines (`*_due_idx`). The partial indexes on the warning instants
  (`*_warn_idx`) are not chosen today, with 800 open tickets, but they hold open tickets
  only, 16-40 kB, and their size follows the team's workload, not the history: they are
  kept for when the open set grows and they become the most selective.
- **`business_hours.calendar_id`**: looks redundant with the GiST index of the exclusion
  constraint, which starts with `calendar_id`. It is not: the `btree_gist` operator
  class only has `bigint = bigint`, and Django writes the id as an `integer` literal,
  so the GiST index cannot serve the lookup.
- **Foreign key indexes** (`category`, `sla_policy`, `requester`, `actor`, `author`):
  kept, so that the check made by `PROTECT` before a delete does not read the whole
  table.
- **BRIN on `ticket.created_at`**: much smaller, but it cannot return rows in order,
  and the staff ticket list (`ORDER BY created_at DESC LIMIT 26`) needs the B-tree.

To try an index or a setting without changing the database, write it to a file and run
`make bench-queries ARGS="--setup file.sql"`: everything runs in a transaction that is
rolled back.
