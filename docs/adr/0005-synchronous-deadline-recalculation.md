# ADR-0005: Deadlines recalculated synchronously when a calendar changes

- Status: Accepted
- Date: 2026-11-23
- Amends: [ADR-0002](0002-sla-business-time-stored-due-dates.md), which said a background
  job would recalculate deadlines after a calendar change.

## Context

SLA deadlines are stored on each ticket (ADR-0002). When a client's calendar changes (a
holiday added, working hours or the time zone changed) or an organization switches to
another calendar, the deadlines of its open tickets must be moved: the business time
already consumed stays as it was, and the business time that was left is placed again
with the new calendar. Deadlines that have already been breached never move.

Measuring "the business time that was left" needs the calendar as it was **before** the
change. ADR-0002 planned a background job for the recalculation. Working out the details
showed two problems with it:

- The job would need a snapshot of the old calendar, taken and passed along when the
  change is saved.
- Between the change and the job, a ticket can change state: a transition run in that
  window computes with the new calendar, and the job then reprojects a deadline that
  already mixes both calendars.

A third case appears whatever the timing: a ticket created while the calendar changes.
Ticket creation reads the calendar before locking the organization, so it could compute
its deadlines with the old schedule after the recalculation has run, and the
recalculation would not include it because it did not exist yet.

## Decision

The recalculation runs **in the same transaction that saves the change**, in the admin
(the only interface that changes calendars).

- **Snapshot under lock.** Before saving, `lock_and_snapshot(calendar_id)` takes the
  calendar's exclusive lock and reads its schedule. Two edits of the same calendar are
  serialized, so each one starts from the state the previous one committed.
- **Reprojection.** `business_time.reproject(anchor, due, old, new)` measures the
  business minutes from the anchor to the deadline with the old schedule and adds them
  with the new one. If both give the same business time, the deadline does not move
  (otherwise the truncated seconds would move every open deadline by up to a minute on
  any change).
- **Anchors.** With the clock running (`new`, `open`), the anchor is now. With the clock
  paused (`pending`, `resolved`), it is `sla_paused_at`: resuming will add the pause
  measured with the new calendar, so anchoring at now would count the stretch since the
  pause twice. The first response deadline is reprojected from now in `new`, `open` and
  `pending` (its clock never pauses) while it has not been reached.
- **Breaches stand.** A deadline already passed (at now, or at `sla_paused_at` for a
  paused ticket) is never moved, even by a holiday declared after the fact.
- **History.** Each ticket whose deadlines move gets an `sla_recalculated` event with no
  actor, the old and new deadlines in UTC and what changed (the calendar, or the
  organization's switch of calendars).
- **Row locks.** The affected tickets are locked in one query ordered by id, and only
  them (`select_for_update(of=("self",))`): the filter joins `organization`, whose rows
  ticket creation locks. Transitions, triage and comments lock a single ticket, so no
  cycle of waits can form.
- **Organizations switching calendars** lock both calendars in id order, so two switches
  in opposite directions cannot wait for each other.
- **Ticket creation** holds the calendar's shared lock while it reads the schedule and
  computes the deadlines, and checks the organization's calendar again under the
  organization lock. A change in progress makes it wait and read the new schedule; a
  change that starts later waits for the ticket to commit and includes it.
- **Advisory locks, not row locks.** The calendar locks are PostgreSQL advisory locks
  (`pg_advisory_xact_lock` and its shared variant, namespace 1 = business calendar). A
  row lock (`SELECT ... FOR SHARE`) must pass the UPDATE policies of row-level security,
  which only admins pass on catalogs: for a client it returns no rows and silently locks
  nothing. Advisory locks are not tied to rows, so RLS does not apply to them.

## Alternatives considered

### Background job (Celery), as ADR-0002 planned

It keeps saves fast with many tickets. Rejected for now: it needs the old schedule
serialized into the task and has the window described above, which would need extra
machinery (version checks, retries) to close. The volume of a portfolio deployment does
not justify it. If calendars with thousands of open tickets appear, this is the
alternative to revisit, with the same reprojection function.

### Recalculating deadlines when they are read

Always consistent with the current calendar, nothing stored. Rejected by ADR-0002 for
the same reasons as before: deadlines could not be indexed and the dashboard queries
could not be plain SQL.

### Making the organization's calendar read-only after creation

It removes one of the two paths. Rejected because changing a client's support hours is
a normal operation; it would force recreating the organization.

### A row-level security policy that lets everyone lock calendar rows

`FOR UPDATE USING (true) WITH CHECK (admins)` would make `FOR SHARE` work for clients
without letting them modify calendars. Rejected because it lets any client lock catalog
rows, mixing "who may lock" with "who may modify" in the security policies, to solve an
internal coordination problem that advisory locks solve without widening anyone's
access.

## Consequences

### Positive

- No window: a ticket never ends up with a deadline computed from a calendar it no
  longer has.
- The recalculation is atomic with the change: if it fails, the calendar change is
  rolled back too.
- Every move of a deadline is in the ticket history.

### Negative and risks

- Saving a calendar takes longer the more open tickets use it, and holds those tickets
  locked meanwhile; ticket creation for its organizations waits too.
- Advisory locks are not visible in the schema: the contract (namespace 1, exclusive to
  change, shared to create) lives in the code and in this ADR.
- Changes made outside the admin (Django shell, raw SQL) do not recalculate anything.

### Mitigations

- `lock_and_snapshot` and `share_calendar_lock` are the only functions that take the
  calendar locks; they refuse to run outside a transaction.
- Tests with real connections cover a ticket created during a calendar change and
  during an organization's switch, running as a client.
- `docs/development.md` and the shell examples use the admin path for calendar changes.
