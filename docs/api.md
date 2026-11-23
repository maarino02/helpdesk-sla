# API reference

A JSON API under `/api/`. Every endpoint requires a valid access token unless it says
otherwise. Request and response bodies are JSON; dates are ISO 8601 in UTC.

## Contents

- [Authentication](#authentication)
- [Endpoints](#endpoints)
- [Who can do what](#who-can-do-what)
- [Tickets](#tickets)
- [Comments and history](#comments-and-history)
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
| `POST /api/tickets/{id}/comments/` | Any user | `{"body": ..., "is_internal": false}`; `201` with the comment. |
| `GET /api/tickets/{id}/events/` | Any user | The ticket history, oldest first. |
| `GET /api/categories/` | Any user | Active categories, to fill the creation form. Not paginated. |

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
  "first_response_due_at": "2026-11-23T11:00:00Z",
  "resolution_due_at": "2026-11-23T18:00:00Z",
  "first_response_at": "2026-11-23T10:25:00Z",
  "resolved_at": null,
  "closed_at": null,
  "created_at": "2026-11-23T10:00:00Z",
  "updated_at": "2026-11-23T10:25:00Z"
}
```

`reference` is the per-client number clients see. People appear only as `id` and
`full_name`. Deadlines already include every pause of the SLA clock (see
[ADR-0002](adr/0002-sla-business-time-stored-due-dates.md)).

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

Other responses follow Django REST Framework: `401` without a valid token, `403` with
`{"detail": ...}` when the endpoint is not for the user's role, `404` for tickets the user
may not see, `405` for a method the endpoint does not support (such as `PUT`), and `429`
when the login attempts are exhausted.
