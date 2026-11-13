# ADR-0002: SLA deadlines calculated in business time and stored on the ticket

- Status: Accepted
- Date: 2026-11-13

## Context

Each ticket has two SLA deadlines that depend on its priority: one for the first
response and one for the resolution. Both are measured in business time, not in
wall-clock time: a 4-hour resolution SLA means four working hours of the support team.

Calculating business time is not trivial:

- Each client has its own business calendar, with a time zone, working hours that can
  include split shifts, and its own holidays.
- The resolution clock pauses while the ticket is waiting for the client (`pending`) or
  is `resolved`, and resumes if the client replies or reopens it.
- The priority of a ticket can change during its life.
- Daylight saving time changes twice a year, so the UTC offset of working hours changes
  too.

Three parts of the system need the deadlines, with different requirements:

- The user interface shows the remaining time of each ticket.
- A background job runs every minute to find tickets about to breach their SLA. It must
  be cheap, because it scans every open ticket.
- The analytics dashboard computes SLA compliance and average response and resolution
  times over thousands of tickets, using hand-written SQL.

## Decision

SLA deadlines are calculated in Python and stored on the ticket, in
`first_response_due_at` and `resolution_due_at`. They are only recalculated when an
event changes them.

- **Business-time arithmetic as pure functions.** `sla/business_time.py` contains two
  functions without database access: `add_business_minutes(start, minutes, calendar)`
  and `business_minutes_between(start, end, calendar)`. Given the same inputs they always
  return the same output, so every edge case can be covered by fast unit tests.
- **Time zones.** All instants are stored as `timestamptz` in UTC. The calendar's time
  zone is only used to decide whether an instant falls within working hours. Working
  hours are defined in local time, so 9:00 in Madrid is 08:00 UTC in winter and
  07:00 UTC in summer.
- **Out-of-hours tickets.** A ticket can be created at any time, but its clock only
  starts at the next working slot. With working hours of 9:00-18:00, a critical ticket
  created on Friday at 17:30 with a 4-hour resolution SLA consumes 30 minutes on Friday
  and is due on Monday at 12:30.
- **When deadlines change.** On creation; on a priority change, where the difference
  between the old and the new duration is applied in business minutes; when the clock
  resumes after a pause; and when the client's calendar changes.
- **Pauses.** Entering `pending` or `resolved` stores the current instant in
  `sla_paused_at`. When the clock resumes, the business minutes elapsed since then are
  added to the deadline. If a ticket waits in `pending` from Monday 10:00 to Monday
  13:00, `resolution_due_at` moves forward three business hours. If it waits from Monday
  17:00 to Tuesday 10:00, it moves forward only two (17:00-18:00 and 9:00-10:00).
- **Calendar changes.** If a holiday is added or working hours change, the deadlines of
  tickets whose clock is running or paused are recalculated by a background job: the
  remaining business time is kept and projected again with the new calendar, while the
  time already consumed is not modified. Deadlines that have already expired are never
  changed: a breach that has happened stays a breach, so a calendar change cannot erase
  it. Each recalculation is recorded as an `sla_recalculated` event. The SLA measures the availability of the support team, and
  counting a day when nobody works would produce false breaches.
- **Compliance.** A deadline is met if `first_response_at <= first_response_due_at`, or
  `resolved_at <= resolution_due_at`. Since stored deadlines already include pauses and
  calendar changes, SQL only needs to compare columns.
- **Business durations for the dashboard.** When the first response and the resolution
  happen, the business minutes consumed (excluding pauses) are also stored in
  `first_response_business_minutes` and `resolution_business_minutes`. The dashboard
  reports averages in business time as its main metric, and wall-clock time
  (`resolved_at - created_at`) as a secondary one, clearly labeled.

## Alternatives considered

### Calculate deadlines on the fly

Deadlines are computed whenever they are needed, from the creation time, the policy,
the calendar and the pause history stored in the events table. They are always
consistent with the current calendar and need no stored columns.

Rejected because each calculation needs the full history of a ticket. The background
job and the dashboard would repeat it for thousands of tickets every time, deadlines
could not be indexed, and the dashboard queries could not be written in plain SQL.

### PL/pgSQL function in the database

Business-time arithmetic is implemented as a database function, usable directly from
SQL, including the dashboard. There is a single implementation, close to the data.

Rejected because this logic has many edge cases (split shifts, holidays, daylight saving
time, out-of-hours starts) and is much easier to write, debug and test in Python with
`zoneinfo` and pytest than in PL/pgSQL. It would also split business logic between two
languages. Storing business durations as columns gives the dashboard what it needs
without this function.

### Elapsed-time counter updated by a periodic job

Each ticket stores the business minutes consumed, and a job adds the elapsed working
minutes to every open ticket every minute. It is a direct model of a running clock.

Rejected because accuracy depends on the job running on time: any delay or downtime
makes counters drift and breaches are detected late. It also writes to every open
ticket every minute, creating constant load for no benefit.

## Consequences

### Positive

- The alerts job and the dashboard use plain comparisons on indexed columns, including
  partial indexes on tickets with a running clock.
- All business-time logic lives in one small module of pure functions that is easy to
  test exhaustively.
- Compliance and business durations can be computed with hand-written SQL, without
  reimplementing business time in the database.
- Every deadline change is traceable in the events table.

### Negative and risks

- Stored deadlines are derived data: a code path that changes the priority, the status
  or a calendar and forgets to recalculate leaves them wrong.
- Business-time arithmetic is error-prone: daylight saving time, split shifts, holidays,
  and spans over midnight or weekends.
- Recalculating after a calendar change can move the deadlines of existing tickets,
  which may surprise agents.
- Two additional columns must be kept consistent with the rest of the ticket.

### Mitigations

- All deadline changes go through `sla/services.py`, called only from the state machine
  and from the admin actions that change priorities or calendars. No other code writes
  the deadline columns.
- `business_time.py` has a dedicated parametrized test suite: Friday at the end of the
  day, holidays, overnight spans, split shifts, tickets created out of hours and both
  daylight saving time changes.
- Recalculations are recorded as `sla_recalculated` events with the old and the new
  deadlines, only the remaining time is projected again, and expired deadlines are never
  modified.
- Database constraints enforce consistency, for example that a business duration is
  set only when its corresponding timestamp is set.
