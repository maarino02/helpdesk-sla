# Analytics

The dashboard metrics are hand-written SQL in `backend/apps/analytics/sql/`. Their
parameters and responses are in the [API reference](api.md#analytics).

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
