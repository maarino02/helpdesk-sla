# API reference

A JSON API under `/api/`. Every endpoint requires a valid access token unless it says
otherwise. Request and response bodies are JSON; dates are ISO 8601 in UTC.

## Contents

- [Authentication](#authentication)
- [Endpoints](#endpoints)
- [Who can do what](#who-can-do-what)
- [Tickets](#tickets)
- [Comments and history](#comments-and-history)
- [Analytics](#analytics)
- [Pagination](#pagination)
- [Errors](#errors)

## Authentication

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/token/` | `email` and `password` → `access` (15 min) and `refresh` (1 day). No token needed. Throttled to 10 attempts per minute per client address. |
| `POST /api/auth/token/refresh/` | `refresh` → new `access`, only while the user is still active. No rotation. |
| `GET /api/me/` | The authenticated user, with role and organization as they are now. |

Send the access token as `Authorization: Bearer <token>`.

The token carries only the user id. Role and organization are read from the database on
every request, and that request runs with the matching row-level security context, so a
change of role or a deactivation applies to the next request with the same token. See
[ADR-0004](adr/0004-rls-request-context.md).

### Browser sessions

The web pages do not keep the refresh token where scripts can read it
([ADR-0006](adr/0006-browser-tokens-refresh-cookie.md)). These three endpoints take and
return JSON only (anything else is a `415`) and need no access token:

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/session/` | `email` and `password` → `access` in the body; the refresh token in an `HttpOnly`, `SameSite=Strict` cookie limited to `/api/auth/session/`. Same throttle as the token login, shared with it. |
| `POST /api/auth/session/refresh/` | With the cookie → new `access`. `401` with code `no_session` without it. |
| `POST /api/auth/session/logout/` | Removes the cookie; `204`. |

```sh
curl -s -X POST http://127.0.0.1:8000/api/auth/token/ \
     -d email=ana@acme.com -d password=secret
curl -s http://127.0.0.1:8000/api/me/ -H "Authorization: Bearer <access>"
```

## Endpoints

| Method and path | Who | Result |
|---|---|---|
| `GET /api/tickets/` | Any user | Tickets the user may see, newest first, filtered and paginated. |
| `POST /api/tickets/` | Clients | `201` with the new ticket and a `Location` header. |
| `GET /api/tickets/{id}/` | Any user | One ticket. |
| `PATCH /api/tickets/{id}/` | Agents, admins | Change `assignee`, `priority` or `category`; `200` with the ticket. |
| `POST /api/tickets/{id}/transitions/` | Depends on the transition | `{"to_status": ...}`; `200` with the ticket. |
| `GET /api/tickets/{id}/comments/` | Any user | The conversation, oldest first. Clients never see internal notes. |
| `POST /api/tickets/{id}/comments/` | Any user | `{"body": ..., "is_internal": false}`; `201` with the comment. A public comment emails the other side of the conversation ([notifications](background-jobs.md#email-notifications)). |
| `GET /api/tickets/{id}/events/` | Any user | The ticket history, oldest first. |
| `GET /api/categories/` | Any user | Active categories, to fill the creation form. Not paginated. |
| `GET /api/staff/` | Agents, admins | Active agents and admins by name, `{id, full_name, role}`: whom a ticket can be assigned to. Not paginated. |
| `GET /api/organizations/` | Agents, admins | Every client, active ones first, by name, `{id, name, is_active}`: the client filter of the analytics. Deactivated clients are listed, since their past metrics remain. Not paginated. |
| `GET /api/analytics/sla-compliance/` | Any user | SLA compliance per month and priority. See [Analytics](#analytics). |
| `GET /api/analytics/response-times/` | Any user | Median and p90 response and resolution times per month and priority. |
| `GET /api/analytics/open-ticket-aging/` | Any user | Open tickets by age right now, with how many have breached their SLA. |
| `GET /api/analytics/category-trends/` | Any user | Tickets created per month and category, with the change from the month before and the rank. |
| `GET /api/analytics/agent-workload/` | Agent, admin | Open work now and resolutions over the period, per staff member. |
| `GET /api/analytics/daily-backlog/` | Any user | Open tickets at the end of each day of the period, rebuilt from the ticket history. |

"Any user" means any authenticated user, but only for the tickets they may see: clients
see every ticket of their organization, agents and admins see all of them. A ticket the
user may not see is a `404`, never a `403`, so its existence is not revealed.

## Who can do what

| Action | Client | Agent | Admin |
|---|---|---|---|
| See tickets | Their organization's | All | All |
| Create a ticket | Yes | No | No |
| Change assignee, priority, category | No | Yes | Yes |
| Write a public comment | Yes | Yes | Yes |
| Write an internal note | No | Yes | Yes |
| See internal notes | No | Yes | Yes |
| See the history | Yes | Yes | Yes |

Status changes follow the transition table in
[`state-machine.md`](state-machine.md#transitions): for example, only a client (or the
system) closes a resolved ticket, and nobody leaves `closed`.

## Tickets

### List

`GET /api/tickets/` returns a page of tickets without their `description`. Optional
filters, combined with AND:

| Parameter | Values |
|---|---|
| `status` | `new`, `open`, `pending`, `resolved`, `closed` |
| `priority` | `low`, `medium`, `high`, `critical` |
| `assignee` | `me`, `none` (unassigned) or a user id |

An invalid value is a `400`, not ignored: unfiltered results would otherwise look
filtered.

### Representation

```json
{
  "id": 42,
  "reference": "ACME-7",
  "number": 7,
  "title": "No puedo entrar en el correo",
  "description": "Me dice que la contraseña es incorrecta.",
  "status": "open",
  "priority": "high",
  "organization": {"id": 3, "name": "Acme"},
  "category": {"id": 2, "name": "Correo"},
  "requester": {"id": 15, "full_name": "Ana López"},
  "assignee": {"id": 4, "full_name": "Luis Pérez"},
  "sla_state": "at_risk",
  "first_response_due_at": "2026-11-23T11:00:00Z",
  "resolution_due_at": "2026-11-23T18:00:00Z",
  "first_response_at": "2026-11-23T10:25:00Z",
  "resolved_at": null,
  "closed_at": null,
  "created_at": "2026-11-23T10:00:00Z",
  "updated_at": "2026-11-23T10:25:00Z",
  "allowed_transitions": ["pending", "resolved"]
}
```

`reference` is the per-client number clients see. People appear only as `id` and
`full_name`. Deadlines already include every pause of the SLA clock (see
[ADR-0002](adr/0002-sla-business-time-stored-due-dates.md)). The list shows the same
fields except `description` and `allowed_transitions`.

`sla_state` is where the ticket stands against its SLA when the response is built, one
instant for the whole response, with the rules of the alert job and the dashboard:

| Value | Meaning |
|---|---|
| `on_track` | Nothing due yet. |
| `at_risk` | A warning instant has come: the first response is still missing, or the resolution clock is running (`new`, `open`), and less than 20 % of its time is left. |
| `paused` | `pending`, waiting for the client, with no breach. |
| `breached` | A deadline came first: unanswered or answered at or after the first response deadline, or not resolved before the resolution deadline (for a pending ticket, before its clock stopped). A breach stays. |
| `met` | Resolved or closed with both deadlines met. |

`allowed_transitions` (detail, and the answers of `PATCH`, transitions and creation)
lists the statuses the requesting user may move the ticket to now, from the state
machine itself: the transitions endpoint accepts exactly these.

### Create

`POST /api/tickets/` with `title`, `description`, `category` (an active category id)
and `priority`. Status, number and deadlines are set by the server. Only clients create
tickets, always for their own organization.

### Change assignee, priority or category

`PATCH /api/tickets/{id}/` with any of `assignee` (an active agent or admin, or `null`
to unassign), `priority` and `category`. Omitted fields do not change, and sending a
field's current value records nothing. Any other field is rejected with a `400` (for
`status`, the message points to the transitions endpoint). There is no `PUT`.

A priority change moves the deadlines, except those already breached: lowering the
priority never erases a breach. The priority is locked once the ticket is resolved, and
a closed ticket cannot be changed at all (`409`).

### Transitions

```http
POST /api/tickets/42/transitions/
{"to_status": "resolved"}
```

`400` for a missing or unknown status, `409` if the transition does not exist from the
current status (the body includes `current_status`), `403` if it exists but not for the
user's role.

## Comments and history

`POST /api/tickets/{id}/comments/` can move the ticket:

- The first public reply of an agent or admin is the first response, and on a `new`
  ticket it also moves it to `open` and assigns it to the author if nobody has it.
- A client reply to a `pending` ticket moves it back to `open`.
- Internal notes never move the ticket nor count as a response.
- A client comment on a `resolved` ticket does not reopen it: reopening is the
  `resolved → open` transition.
- Closed tickets accept no comments (`409`).

The response is the comment only; read the ticket again to see its new status.

Each history entry has `event_type`, `actor` (`null` for the system), `from_value` and
`to_value` as stored (ids and codes), `from_display` and `to_display` as people read them
(names and Spanish labels), `metadata` and `created_at`. Event types: `created`,
`status_changed`, `assignee_changed`, `priority_changed` (with the old and new deadlines
in `metadata`), `category_changed` and `sla_recalculated`.

## Analytics

Read-only dashboard metrics, computed by hand-written SQL (`backend/apps/analytics/sql/`).
Every metric takes the same query parameters:

| Parameter | Default | Notes |
|---|---|---|
| `start` | First day of the month eleven months before `end` | `YYYY-MM-DD`, included. |
| `end` | Today | `YYYY-MM-DD`, included. At most 1,098 days after `start`. |
| `organization` | Every client (staff); the user's own (clients) | A client asking for another organization gets `403 other_organization`; staff asking for one that does not exist get `400`. The ids come from `GET /api/organizations/`. |

Days and months are those of the provider's time zone (`Europe/Madrid`) for every
client, so that their figures add up: a ticket answered at 23:30 on 31 March in Mexico
City counts in April. The response echoes the period used, then the rows:

```json
{
  "period": {"start": "2025-12-01", "end": "2026-11-24", "organization": null,
             "time_zone": "Europe/Madrid"},
  "results": [...]
}
```

### SLA compliance

`GET /api/analytics/sla-compliance/`: one row per month of the period and priority
(`low`, `medium`, `high`, `critical`), plus `all` for the month's total:

```json
{"month": "2026-03-01", "priority": "medium",
 "first_responses": 3, "first_responses_met": 2, "first_response_rate": 66.7,
 "resolutions": 2, "resolutions_met": 1, "resolution_rate": 50.0}
```

- Each outcome counts in the month it happened: a first response in the month of the
  reply, a resolution in the month of the (last) resolution. A finished month's figures
  do not change afterwards.
- Met means before the deadline, as the alert job judges it: a reply exactly at the
  deadline is a breach. Resolving at 20:00 a ticket due at 18:00 is a breach even though
  no business minutes passed after 18:00.
- Months without outcomes come back with zero counts and `null` rates: no data is not
  0 %.

### Response times

`GET /api/analytics/response-times/`: the same grid of months and priorities, with the
times in minutes:

```json
{"month": "2026-03-01", "priority": "medium",
 "first_responses": 4, "first_response_median": 25.0, "first_response_p90": 79.0,
 "first_response_median_wall": 150.0,
 "resolutions": 3, "resolution_median": 200.0, "resolution_p90": 520.0,
 "resolution_median_wall": 2000.0}
```

- `median` and `p90` are business minutes, the time the SLA counts: from creation to the
  first public staff reply, and to the resolution without the pauses (waiting for the
  client, or resolved until reopened). The p90 is how long the slowest tenth took.
- `median_wall` is wall-clock minutes from creation, nights, weekends and pauses
  included: what the client lived.
- Percentiles interpolate between neighbouring values (`percentile_cont`): the median
  of 10 and 20 minutes is 15. The `all` row is the percentile of every ticket of the
  month, not an average of the priorities' figures.
- As in SLA compliance, each outcome counts in the month it happened, and a cell without
  outcomes has `null` times.

### Open ticket aging

`GET /api/analytics/open-ticket-aging/`: a snapshot of the work still to do, so it takes
`organization` but no period (`start` or `end` are a `400`). One row per age bucket,
youngest first, empty buckets included:

```json
{
  "as_of": "2026-11-24T12:00:00Z",
  "organization": null,
  "results": [
    {"bucket": "under_1_day", "min_days": 0, "max_days": 1, "tickets": 2,
     "new": 1, "open": 1, "pending": 0,
     "resolution_breached": 0, "first_response_overdue": 1},
    ...
  ]
}
```

- Open means `new`, `open` or `pending`. A resolved ticket is waiting for the client's
  confirmation, not for the support team, and is not counted.
- Age is wall-clock time since creation; buckets are `[min_days, max_days)`, so a ticket
  exactly one day old is in `1_to_3_days`. `over_7_days` has `max_days: null`.
- `resolution_breached`: the deadline came before the clock stopped. A pending ticket
  paused before its deadline is not breached.
- `first_response_overdue`: not answered yet and past the deadline. A late first
  response that has already happened is counted by SLA compliance instead.

### Category trends

`GET /api/analytics/category-trends/`: one row per month of the period and category,
by month, then rank, then name:

```json
{"month": "2026-03-01", "category_id": 2, "category": "Acceso",
 "tickets": 4, "previous": 2, "change": 2, "change_pct": 100.0,
 "share": 44.4, "rank": 1}
```

- `tickets` counts the tickets created in the month under their current category (the
  one triage settled on).
- `previous` is the month before, also for the first month of the period, which is
  compared with the month before it. `change_pct` is `null` when `previous` is 0:
  growing from nothing is no percentage.
- `share` is the percentage of the month's tickets (`null` in a month without any);
  `rank` is the position within the month, ties sharing it and skipping the next (1, 1,
  3).
- Months are counted whole: a `start` on the 15th still counts its month from the 1st,
  so that every month compares like with like.
- Every active category is listed, with zeros where it has no tickets; inactive ones
  only if they have tickets in those months.

### Agent workload

`GET /api/analytics/agent-workload/`: staff only (a client gets `403`). It takes the
usual parameters, and mixes two kinds of figures, so the response carries both the
instant and the period:

```json
{
  "as_of": "2026-11-24T12:00:00Z",
  "period": {"start": "2026-11-01", "end": "2026-11-30", "organization": null,
             "time_zone": "Europe/Madrid"},
  "results": [
    {"agent_id": 7, "full_name": "Lucía Gómez", "role": "agent", "is_active": true,
     "open_tickets": 3, "pending": 1, "open_breached": 1,
     "resolved": 12, "resolution_median": 240.0, "resolved_late": 2},
    ...
    {"agent_id": null, "full_name": null, "role": null, "is_active": null,
     "open_tickets": 4, "pending": 0, "open_breached": 0,
     "resolved": 0, "resolution_median": null, "resolved_late": 0}
  ]
}
```

- `open_tickets`, `pending` and `open_breached` are a snapshot at `as_of`, whatever the
  period: tickets `new`, `open` or `pending` assigned to the person, and those whose
  resolution deadline came before the clock stopped (as in open ticket aging).
- `resolved`, `resolution_median` (business minutes) and `resolved_late` cover the
  tickets resolved in the period. Late means at or after the deadline, as in SLA
  compliance.
- A ticket counts for its current assignee. Who resolved it is in the ticket history.
- Every active agent and admin is listed, with zeros if they have nothing (also when
  `organization` narrows the figures to one client). Deactivated users appear only while
  they still hold tickets. Tickets without an assignee have their own row, `agent_id:
  null`, always last; the rows add up to the totals of the other metrics.
- Busiest first (most open tickets), then by name.

### Daily backlog

`GET /api/analytics/daily-backlog/`: one row per day of the period up to today, oldest
first, with the tickets open at the end of the day (today: so far). Days are those of
the provider's time zone, and the response carries `as_of` next to the period, as in
agent workload:

```json
{"day": "2026-11-22", "backlog": 4, "new": 1, "open": 3, "pending": 0,
 "created": 1, "resolved": 0}
```

- `backlog` is `new + open + pending`, the work still to do; `created` and `resolved`
  are what came in and went out that day (a ticket resolved twice counts twice).
- The past is rebuilt from the ticket history, not read from the tickets, which only
  know their current status: today's figures always equal the open tickets now.
- A period ending in the future stops at today.

## Pagination

Lists of tickets, comments and events use cursor pagination:

```json
{"next": "http://.../api/tickets/?cursor=cD0y...", "previous": null, "results": [...]}
```

Follow `next` until it is `null`; do not build cursors by hand. Tickets come 25 per page,
newest first; comments and events 50 per page, oldest first. There is no total count and
no jumping to page N: in exchange, every page costs the same however far it is, and
tickets created while paging are neither repeated nor skipped.

## Errors

Validation errors (`400`) list the problems by field, or under `non_field_errors` when
they concern no single field:

```json
{"category": ["Clave primaria \"9\" inválida - objeto no existe."]}
{"non_field_errors": ["La organización no está activa."]}
```

Business rule errors (`403` and `409`) have a message for the user, a stable `code` for
programs, and sometimes extra fields:

```json
{
  "detail": "La incidencia está en estado «Cerrado» y no puede pasar a «Abierto».",
  "code": "transition_not_defined",
  "current_status": "closed"
}
```

| Status | `code` | When |
|---|---|---|
| `409` | `transition_not_defined` | The transition does not exist from the current status. Includes `current_status`. |
| `409` | `ticket_closed` | Changing a closed ticket. Includes `current_status`. |
| `409` | `priority_locked` | Changing the priority of a resolved ticket. Includes `current_status`. |
| `409` | `comments_closed` | Commenting on a closed ticket. Includes `current_status`. |
| `403` | `transition_not_allowed` | The transition exists, but not for the user's role. |
| `403` | `staff_only` | Only agents and admins may change a ticket. |
| `403` | `internal_notes_staff_only` | A client writing an internal note. |
| `403` | `comment_not_allowed` | Commenting on another organization's ticket. |
| `403` | `other_organization` | A client asking for another organization's metrics. |

Other responses follow Django REST Framework: `401` without a valid token, `403` with
`{"detail": ...}` when the endpoint is not for the user's role, `404` for tickets the user
may not see, `405` for a method the endpoint does not support (such as `PUT`), and `429`
when the login attempts are exhausted.
