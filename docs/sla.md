# SLA and business time

How deadlines are computed and how they move. The decisions behind these rules are in
[ADR-0002](adr/0002-sla-business-time-stored-due-dates.md) and
[ADR-0005](adr/0005-synchronous-deadline-recalculation.md); the statuses and
transitions in [`state-machine.md`](state-machine.md). The code is
`backend/apps/sla/business_time.py` (pure functions) and the ticket services.

## Contents

- [Business calendars](#business-calendars)
- [What a business minute is](#what-a-business-minute-is)
- [Deadlines](#deadlines)
- [How deadlines move](#how-deadlines-move)
- [Measuring compliance](#measuring-compliance)
- [Alerts](#alerts)

## Business calendars

Each organization has a calendar (several can share one) with:

- **A time zone** (IANA name, such as `Europe/Madrid`). Working hours are local times in
  that zone; every timestamp in the database stays in UTC.
- **Working slots per weekday.** Several per day allow split shifts (09:00-14:00 and
  15:00-18:00). Slots never overlap (an exclusion constraint) and never cross midnight.
  As an end, `00:00` means 24:00: `22:00-00:00` is a night shift until midnight and
  `00:00-00:00` a whole day, so a 24/7 calendar is seven of them. A shift across
  midnight is two slots, one on each day.
- **Holidays**: local dates on which nothing is worked.

## What a business minute is

A real minute inside a working slot, on a day that is not a holiday, in the calendar's
time zone.

- **Real time, also across daylight saving changes.** A local time that does not exist
  (spring) moves forward by the length of the gap; one that happens twice (autumn) is
  its first occurrence (the rule of `java.time`). So in Madrid a `01:00-04:00` slot lasts
  2 hours on 29 March 2026 and 4 hours on 25 October 2026. A slot entirely inside the
  skipped hour does not exist that day.
- **Out of hours counts nothing.** With working hours 09:00-18:00, a pause from Monday
  17:00 to Tuesday 10:00 is 2 business hours.
- **Measures are whole minutes**, truncated towards zero, and signed: the time left
  until a deadline that has already passed is negative.

## Deadlines

When a ticket is created, its policy (the organization's for that priority, or the
default one) gives two durations in business minutes. Each deadline is the creation
instant plus that duration in business time:

- A ticket created out of hours starts counting at the next working slot. With
  09:00-18:00, Monday to Friday, a 4-hour deadline from Friday 17:30 consumes 30 minutes
  on Friday and is due on Monday at 12:30.
- A deadline used up exactly at the end of a slot is that end (18:00), not the start of
  the next slot (09:00 the next day): the instant closest to the start.
- The resolution duration is also copied to the ticket (`resolution_target_minutes`):
  what was promised to this ticket, whatever later edits of the policy say.

## How deadlines move

Deadlines are stored, so every event that changes them rewrites them, and every rewrite
leaves an event in the ticket history.

| Event | First response deadline | Resolution deadline |
|---|---|---|
| `open → pending`, `→ resolved` | Unchanged (this clock never pauses) | The clock pauses: `sla_paused_at` is set |
| Resuming (`pending → open`, `resolved → open`, `pending → resolved`) | Unchanged | Moved later by the business minutes of the pause |
| Priority change (`new`, `open`, `pending`) | Computed again from creation, if not answered yet | Moved by the difference between the new policy and the ticket's target |
| Calendar change, or the organization switches calendars | Reprojected from now, while it runs | Reprojected from now (running) or from `sla_paused_at` (paused) |
| `→ closed` | Unchanged | Unchanged; the clock stops for good |

"Reprojected" means: the business time left until the deadline, measured with the old
calendar, is placed again with the new one. Time already consumed is never touched.

Two rules apply to every move:

- **A breach stands.** A deadline that has already passed (judged at `sla_paused_at` for
  a paused ticket) never moves: neither a lower priority nor a holiday declared after the
  fact can erase it. A higher priority can move a deadline into the past: a more urgent
  ticket can be overdue already.
- **No move without a reason.** If the old and new calendars give the same business time
  until a deadline, it stays exactly where it is.

## Measuring compliance

Deadlines already include every pause, priority change and calendar change, so the
dashboard compares columns:

- First response met: `first_response_at <= first_response_due_at`.
- Resolution met: `resolved_at <= resolution_due_at`.

The business minutes consumed are stored when they happen:

- `first_response_business_minutes`: from creation to the first public reply of the
  support team (internal notes do not count).
- `resolution_business_minutes`: `resolution_target_minutes` minus the business minutes
  left until the deadline at the moment of resolving. Pauses are excluded without
  storing them: they are already in the deadline. A late resolution consumes more than
  the target.

## Alerts

Each ticket also stores the instant at which 80 % of each deadline's business time is
consumed (`first_response_warning_at`, `resolution_warning_at`). The budget of the
resolution is the ticket's target; the first response clock never pauses, so its budget is
the business time from creation to its deadline. These instants are rewritten together
with the deadlines, in every case of the table above, and can never come after them.

A job compares them with the current time every minute and records a warning (80 %
consumed, deadline still ahead) or a breach (deadline passed) while the clock runs; a
paused clock raises nothing. The job and its guarantees are described in
[`background-jobs.md`](background-jobs.md#sla-alerts).
