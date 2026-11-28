# Background jobs

The Celery tasks: what each one does, when it runs and why running it twice is
harmless. The SLA rules they apply are in [`sla.md`](sla.md); the status changes in
[`state-machine.md`](state-machine.md).

## Contents

- [Overview](#overview)
- [Delivery and idempotency](#delivery-and-idempotency)
- [SLA alerts](#sla-alerts)
- [Auto-close](#auto-close)
- [Email notifications](#email-notifications)
- [Running and checking the jobs locally](#running-and-checking-the-jobs-locally)
- [Testing tasks](#testing-tasks)
- [Known limits](#known-limits)

## Overview

| Task | Trigger | What it does |
|---|---|---|
| `apps.tickets.tasks.raise_sla_alerts` | Beat, every minute (`expires` 60 s) | Records the SLA warnings and breaches that have become due and enqueues their notifications. |
| `apps.tickets.tasks.auto_close_resolved` | Beat, every hour (`expires` 3600 s) | Closes the tickets resolved more than `AUTO_CLOSE_AFTER` (7 calendar days) ago. |
| `apps.tickets.tasks.notify` | After the commit of a status change, a new SLA alert or a public comment | Decides the recipients and enqueues one email per recipient. |
| `apps.tickets.tasks.send_notification` | Enqueued by `notify` | Renders and sends one email; retried on SMTP and network errors. |
| `apps.core.tasks.ping` | Beat, every 5 minutes | Heartbeat: proves the beat → broker → worker chain works. |

The schedule lives in code (`CELERY_BEAT_SCHEDULE` in `config/settings/base.py`).
Every task that reads tickets runs in `system_context()`: Row-Level Security lets it see
every organization, and the context is a durable transaction, so each block commits on
its own.

## Delivery and idempotency

The worker acknowledges a message after running it (`acks_late`, prefetch 1). If a
worker dies mid-task, the message is delivered again: delivery is at least once, so
every task must be safe to run twice.

- **Periodic jobs catch up.** Each run handles everything due at that moment, not only
  what became due since the last run. A run that was missed changes nothing, and a run
  still queued when the next one is sent is redundant: `expires` discards it, so after a
  worker outage the backlog is not replayed run by run.
- **The database decides what is new.** Alerts rely on a unique key; closing relies on
  the status. A second run finds nothing to do.

## SLA alerts

`apps/tickets/services/sla_alerts.py`: four `INSERT ... SELECT` statements, one per
kind and level, run in one transaction with the same `now`.

| Alert | Raised when |
|---|---|
| Resolution warning | Status `new` or `open`, `resolution_warning_at <= now < resolution_due_at` |
| Resolution breach | Status `new` or `open`, `resolution_due_at <= now` |
| First response warning | No first response, status `new`, `open` or `pending`, `first_response_warning_at <= now < first_response_due_at` |
| First response breach | No first response, status `new`, `open` or `pending`, `first_response_due_at <= now` |

- The warning instants (80 % of the business time consumed) are stored on the ticket
  next to the deadlines and rewritten with them, so the job compares columns.
- Each `WHERE` repeats the predicate of a partial index (`status IN ('new', 'open')` or
  `first_response_at IS NULL`); the planner uses a partial index only when the query
  implies its predicate.
- A deadline equal to `now` is a breach. If the job was down during the warning window,
  only the breach is raised, never a late warning.
- `NOT EXISTS` skips tickets already alerted, so a ticket past its warning is not tried
  again every minute (each conflicting insert would still consume a value of the identity
  sequence). `ON CONFLICT (ticket_id, kind, level) DO NOTHING` covers two runs at the
  same time.
- `RETURNING` yields only the rows inserted: each alert is notified once.
- There is at most one alert per ticket, kind and level, even if the deadline is later
  extended.

Measured on 200,000 tickets: 2 to 90 ms per statement. The warnings may use the deadline
index instead of the warning index: the planner misestimates the window
`warning <= now < due` (column statistics cover the whole table, and the two columns are
correlated). Both plans take milliseconds; to be measured again with real data.

## Auto-close

`apps/tickets/services/auto_close.py` and the task `auto_close_resolved`.

1. **Read a batch** (500) of candidates, `status = 'resolved' AND resolved_at <= cutoff`,
   in `(resolved_at, id)` order, after the last ticket of the previous batch. The partial
   index `ticket (resolved_at, id) WHERE status = 'resolved'` serves both the filter and
   the order. The cursor condition is written as
   `resolved_at >= x AND (resolved_at > x OR id > y)` so that the index range starts at
   the cursor; the id breaks ties on `resolved_at` at batch boundaries.
2. **Close each ticket in its own transaction.** The row is locked with
   `SELECT ... FOR UPDATE SKIP LOCKED` and the condition is checked again: between the
   read and the close, the ticket may have been reopened, closed by the client, or
   reopened and resolved again a minute ago. Then `transition(ticket, closed, None)`, like
   any other status change (event with no actor, notification to the requester).
3. **Stop** when a batch comes back shorter than the batch size.

A ticket that someone is changing at that moment is skipped, not waited for; if it is
still stale, the next run closes it. Batches move forward by key, so skipped tickets are
not read again and the loop always ends.

## Email notifications

`apps/tickets/services/notifications.py`, templates in
`apps/tickets/templates/tickets/email/`. Each email has a plain-text part and an HTML
alternative (`base.html` is the shared layout, with inline styles because many email
clients drop `<style>` blocks), and both link to the ticket page at
`SITE_URL/tickets/{id}/`. The HTML templates keep autoescaping on, since titles and
comments are written by users; a test fails if one of them turns it off.

| Source | Recipient | Not sent when |
|---|---|---|
| Status change to `pending`, `resolved` or `closed` | The requester | The requester made the change, or is inactive |
| SLA alert (warning or breach) | The assignee | No active assignee: sent to every active admin instead |
| Public comment by the staff | The requester | The requester is inactive |
| Comment by a client (the requester or a colleague) | The assignee | No active assignee: nobody is notified (the ticket is in the unassigned queue, and the SLA alerts escalate it) |
| Internal note | Nobody | Always |

Flow:

1. The service that writes the source row calls `notify_on_commit(kind, id)`:
   `transition()` for the `ticket_event` of a status change, the SLA job for each new
   `sla_alert`, `add_comment()` for each public `ticket_comment`. The task is enqueued only when the outermost transaction commits; if it
   rolls back, nothing is sent. `robust=True`: if the broker is down, the error is logged
   and the request that already committed still succeeds.
2. `notify(kind, id)` reads the source, decides the recipients with current data and
   enqueues one `send_notification` per recipient.
3. `send_notification(kind, id, recipient_id)` renders the email inside a system context
   and sends it after leaving the transaction, so no database connection waits on SMTP.
   It is retried on `SMTPException` and `OSError`, with exponential backoff, up to 5
   times. `EMAIL_TIMEOUT` (10 s) stops a silent SMTP server from blocking a worker.

Only ids travel through the broker. Every source is immutable (`ticket_event` is insert
only, alerts and comments are never edited), so the email describes what happened at commit time even
if the ticket changed afterwards, and no personal data is stored in Redis.

Deadlines in alert emails are shown in the time zone of the client's business calendar.

## Running and checking the jobs locally

- `make worker` and `make beat` (or `make up-app`). Emails go to the worker log with the
  default `EMAIL_URL=consolemail://`.
- Run a task now instead of waiting for beat:
  `uv run celery --workdir backend -A config call apps.tickets.tasks.raise_sla_alerts`.
- Beat sends each periodic task for the first time one interval after it starts, not at
  startup.
- Never run the local stack (`make worker`/`make beat`) and `make up-app` at the same time:
  two workers share the queue and two beat schedulers send every task twice.

## Testing tasks

- Tasks are tested by calling the function, not through a worker (no
  `CELERY_TASK_ALWAYS_EAGER`). Test settings use an in-memory broker (`memory://`), so a
  test can never enqueue into the development Redis.
- Default tests run inside one transaction that never commits, so `on_commit` callbacks
  never run. Use pytest-django's `django_capture_on_commit_callbacks(execute=True)` to run
  them, and replace `notify.delay` to record what would be enqueued.
- `production_like` tests commit for real; replace `.delay` with the task itself to run
  the whole chain in the test process (`backend/tests/test_e2e_notifications.py`).
- `test_tasks.py` in `apps/core` checks that every beat entry names a registered task and
  that every task is listed in this document.

## Known limits

- **Lost notification:** if the broker is down right after a commit, that notification
  is lost (logged, not raised). A transactional outbox would close the gap: write the
  notification to a table in the same transaction and let a job deliver it.
- **Duplicate email:** a retry after an email that did go out (for example, the SMTP
  server accepted it but the connection dropped before the reply) sends it twice.
- **Alert stuck at the first deadline:** one alert per ticket, kind and level; if a
  deadline is extended after its warning, no second warning is raised.
- **Breach scan:** the breach statements read every overdue ticket whose clock is still
  running on each run; `NOT EXISTS` avoids the insert, not the read.
