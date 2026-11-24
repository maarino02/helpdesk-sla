# Demo data

`make seed` fills an empty database with three client companies, their support
calendars and SLA agreements, the support team, and months of ticket history up to the
current moment. The code is in `backend/apps/demo/`; the commands to run it are in the
[development guide](development.md#demo-data).

## Contents

- [Demo accounts](#demo-accounts)
- [The catalog](#the-catalog)
- [The history](#the-history)
- [Two ways to write it](#two-ways-to-write-it)
- [Resetting](#resetting)
- [Volume for query measurements](#volume-for-query-measurements)
- [How it is tested](#how-it-is-tested)
- [Known limits](#known-limits)

## Demo accounts

Every account uses the password in `DEMO_PASSWORD` (`demo1234` in development; outside
development the command refuses to run without it). Every email uses the reserved
`.example` domain, so no notification can reach a real mailbox.

| Account | Role | Sees |
|---|---|---|
| `admin@soporte.example` | Admin (also Django admin) | Every client; manages calendars, policies and categories |
| `agente@soporte.example` | Agent | Every client's tickets |
| `cliente@alameda.example` | Client of Ferretería Alameda | Only Alameda's tickets |
| `cliente@pacifico.example` | Client of Transportes del Pacífico | Only Pacífico's tickets |
| `cliente@vallebueno.example` | Client of Clínica Vallebueno | Only Vallebueno's tickets |

There are more agents and client users with the same password. The command prints the
demo accounts when it finishes.

## The catalog

Declared as data at the top of `catalog.py`. All names are fictitious.

| Client | Calendar | Time zone | Hours | SLA |
|---|---|---|---|---|
| Ferretería Alameda | Oficina Madrid | `Europe/Madrid` | Mon-Thu 9:00-14:00 and 15:00-18:00, Fri 8:00-15:00 | Default |
| Transportes del Pacífico | Oficina Ciudad de México | `America/Mexico_City` | Mon-Fri 9:00-18:00 | Default |
| Clínica Vallebueno | 24x7 | `UTC` | Every day, all day | Own agreement for critical and high |

- **Holidays:** the fixed-date national and regional ones and, in Mexico, the "nth
  Monday" ones, from the year the history starts to next year.
- **SLA policies** (first response / resolution, business minutes): critical 30/240,
  high 60/480, medium 240/1440, low 480/2880. Vallebueno's agreement: critical 15/120,
  high 30/240.
- **Categories:** six active ones and one retired (`Telefonía fija`), which only old
  tickets have.
- **People:** the admin, five agents (one who left the company and is inactive) and
  nine client users (one inactive). Inactive people only appear on tickets from before
  they left, 90 days before the end of the history.

## The history

`plan.py` decides what happens to each ticket, when and by whom. It does not touch the
database: the same catalog, seed and current instant give the same plan.

- **Arrivals.** 45 % of the tickets for Alameda, 35 % for Pacífico and 20 % for
  Vallebueno. More on Mondays and in the morning (local time), few at weekends except
  for the 24x7 client, and about 40 % more in the last month than in the first.
- **Priorities:** 5 % critical, 20 % high, 50 % medium, 25 % low.
- **First response:** a public reply from an agent, which takes the ticket
  (new → open, assigned to them). Timed in business minutes: within the target, or
  late for 9 % of tickets.
- **Work:** 10 % change priority, 5 % change assignee, 30 % get an internal note, 25 %
  wait for the client (pending), who answers in 85 % of cases. The resolution comes
  within the target, or late for 8 % of tickets. Waiting for the client does not count
  against the SLA (the clock is paused).
- **After resolving:** 8 % are reopened by the client and resolved again. Then 55 % are
  closed by the client within four days, and the rest by the auto-close job seven days
  later (an event with no actor).
- **The present:** steps later than the current moment are dropped. The newest tickets
  stay new, open, pending or resolved, and the SLA alerts due at that moment are raised
  once, as the job would.

About 9-15 % of the tickets breach an SLA: enough for the dashboard to have something to
show, without looking like a failing helpdesk. Past breaches are measured from the
ticket's columns; earlier alerts are not recreated.

## Two ways to write it

| | Through the services (default) | In batches (`--bulk`) |
|---|---|---|
| How | `replay.py` calls `create_ticket`, `add_comment`, `transition` and `update_ticket` with each planned instant as `now` | `bulk.py` computes the same effects in memory and inserts them with `INSERT ... SELECT FROM unnest(...)` |
| Speed | About 100 ms per ticket from Windows (~52 queries) | About 0.7 ms per ticket |
| Use | The demo (1,500 tickets, about 2.5 minutes) | Volume for measurements (200,000 tickets, about 2 minutes) |

Through the services, deadlines, pauses, business minutes and events are computed by
the production code, so the data is consistent by construction. The bulk path copies
those rules. `test_bulk.py` replays one plan both ways and requires identical rows, so a
change to a service fails until `bulk.py` follows. Cases the plan never produces (a
priority change on a paused ticket, for example) are not copied: `bulk.py` raises
instead of guessing.

`COPY FROM` would be faster still, but PostgreSQL rejects it on a table with row-level
security for a role subject to it, and here both roles are (`FORCE ROW LEVEL
SECURITY`). With `INSERT`, the policies, foreign keys and `CHECK` constraints validate
every row, as they do for the services.

Both ways run in one transaction with notifications suppressed: a failure leaves the
database as it was, and nobody is emailed about the made-up history. Progress is
printed as it goes, because other sessions see nothing until the commit.

## Resetting

The command refuses to run if there are clients, calendars, policies or categories
already. A superuser alone does not block it. With `--reset`, it first empties every
business table (tickets, users, clients, calendars...) with one `TRUNCATE` on the
owner's connection, because the application role has no `TRUNCATE` privilege.

- **Explicit list, no `CASCADE`.** A new table that references an emptied one makes the
  `TRUNCATE` fail instead of being emptied without anyone deciding so. A test compares
  the list with every table of the business apps.
- **No `RESTART IDENTITY`.** A token names its user by id only. If ids restarted, a
  refresh token issued before the reset (valid for a day) would authenticate whoever
  got that id afterwards, perhaps an admin. Ids keep growing instead, and old tokens
  point to users that no longer exist.

## Volume for query measurements

```sh
make seed ARGS="--reset --bulk --clients 40 --months 24 --tickets 200000"
```

| What | Amount |
|---|---|
| Clients | 43: the three demo ones and 40 numbered ones that share the three calendars, from about 22,000 tickets to about 1,700 (a long tail) |
| Tickets | 200,000 over 24 months |
| Events / comments | About 1.15 million / 640,000 |
| Size | About 385 MB with indexes |

Run `ANALYZE` afterwards (in `make psql`). The application role cannot, and until
autovacuum gets to the new rows the planner has no statistics: `pg_class.reltuples` is
still 0 after the seed.

## How it is tested

- **The plan** (`test_plan.py`): determinism, the period, the share of each client,
  dropping future steps, retired people and categories only on old tickets, the
  auto-close delay.
- **The stored history** (`test_history.py`), checked as invariants rather than
  examples:
  - the status matches the last status change;
  - the timestamps match the status;
  - the first response is the first public staff reply;
  - numbers follow creation order;
  - `updated_at` is the last activity;
  - no notification is enqueued.
- **The bulk path** (`test_bulk.py`): identical rows to the services for the same plan,
  and a check that the compared plan reaches every kind of step.
- **Reset** (`backend/tests/test_seed_reset.py`): with real commits and the owner's
  connection; ids are never reused.

## Known limits

- Holidays tied to Easter are not included.
- Ticket texts come from a short fixed list per category, so titles repeat.
- Every account shares one password and the demo admin is a Django superuser: fine
  locally, to be revisited for a public demo (phase 10).
- The history ends at the moment the seed runs. Days later, the demo still works (the
  alert and auto-close jobs pick up from there), but the most recent weeks look quiet.
