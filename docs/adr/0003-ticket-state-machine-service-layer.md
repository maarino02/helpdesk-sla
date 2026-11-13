# ADR-0003: Ticket state machine as an explicit transition table in the service layer

- Status: Accepted
- Date: 2026-11-13

## Context

A ticket moves through five statuses: `new`, `open`, `pending`, `resolved` and
`closed`. The allowed transitions and their effects are described in
[`docs/state-machine.md`](../state-machine.md). This ADR records how that state machine
is enforced in the code.

The implementation has to satisfy several requirements:

- **Integrity.** Only defined transitions may happen. A ticket cannot jump from `new`
  to `closed`, and nothing can leave `closed`.
- **Permissions.** Allowed transitions depend on the actor. A client can reopen a
  resolved ticket but cannot resolve one, and some transitions are performed by the
  system itself, such as auto-closing resolved tickets.
- **Side effects.** Each transition updates timestamps and SLA deadlines (see ADR-0002)
  and records an event. These changes must happen together or not at all.
- **Concurrency.** Two actors can act on the same ticket at the same moment, for
  example a client reopening a ticket while the background job is auto-closing it.
- **Several entry points.** Status changes come from the REST API, from the comment
  service (a client reply moves a ticket from `pending` to `open`), from background
  jobs and from the admin site.
- **Testability.** It must be possible to prove that every invalid combination is
  rejected.

## Decision

Every status change goes through a single service function,
`transition(ticket, to_status, actor)`, in `tickets/services/state_machine.py`.

- **Explicit transition table.** A module-level constant, `TRANSITIONS`, maps each
  `(from_status, to_status)` pair to the roles allowed to perform it: `client`, `agent`,
  `admin` or `system`. Any pair missing from the table is invalid.
- **Single entry point.** No other code assigns `ticket.status`. The API exposes the
  status as read-only, and changes are requested through a dedicated endpoint,
  `POST /api/tickets/{id}/transitions/`, with the target status in the body. The comment
  service and the background jobs call the same function.
- **Atomic and serialized.** Inside `transaction.atomic()`, the function locks the
  ticket row with `select_for_update()`, reads the current status, validates the
  transition and the actor's role, applies the timestamp and SLA changes and records a
  `status_changed` event. If any step fails, the whole transaction is rolled back and
  nothing is left half-applied.
- **Concurrent requests.** When two operations compete, the second one waits for the
  lock and is then validated against the status left by the first. If the auto-close
  job closes a ticket first, the client's reopen request is evaluated as
  `closed → open`, which is not allowed, and is rejected.
- **Error responses.** A malformed request or an unknown status returns
  `400 Bad Request`. A transition that exists but is not allowed for the actor's role
  returns `403 Forbidden`. A transition that is not defined from the current status
  returns `409 Conflict`, because the request conflicts with the current state of the
  resource: the client can refresh the ticket and decide again.
- **Database safety net.** A `CHECK` constraint limits `status` to the five valid
  values. The database guarantees that the status is valid; the service guarantees that
  the transition is.

## Alternatives considered

### State machine library

Libraries such as `django-fsm` declare transitions with decorators on model methods.
It is a well-known, declarative pattern.

Rejected because `django-fsm` was archived in April 2024 and is no longer maintained.
Its successor, `viewflow.fsm`, is part of a much larger framework. Adding a dependency
is not justified for something a dictionary and one function solve in a few dozen
lines, especially since role checks, row locking and SLA side effects would still need
custom code.

### Database trigger

A PostgreSQL trigger validates every update of `status`. It is enforced on every write
path, including raw SQL.

Rejected because the SLA calculation lives in Python (ADR-0002), so the effects of a
transition cannot run inside the trigger. The rules would be split between PL/pgSQL and
Python, and role checks would depend on the session context. Triggers are also harder
to test and debug.

### Configurable workflows stored in the database

Transitions are stored in a table that admins can edit, as Jira does with its
workflows. Each client could have its own flow.

Rejected because there is no requirement for per-client workflows. The SLA rules depend
on specific statuses (`pending` and `resolved` pause the clock), so arbitrary workflows
would break them. It would also add a lot of complexity: an editing interface,
validation of the configured flows and data migrations when a flow changes.

### Validation in serializers and views

`status` is a regular writable field, validated in the API serializer. It is the
simplest option.

Rejected because the rules would only apply to the API. The comment service, the
background jobs and the admin site would either duplicate them or skip them, the side
effects would be scattered, and there would be no single place to test.

## Consequences

### Positive

- The workflow is defined in one place, and the `TRANSITIONS` table reads like the
  state diagram.
- Every entry point gets the same validation, side effects and history.
- Race conditions on the same ticket are resolved by the row lock.
- Exhaustive tests are simple: a parametrized test covers the 25 status pairs for each
  of the 4 roles, 100 cases in total.
- No external dependency is needed.

### Negative and risks

- Code can still bypass the service, for example with `ticket.status = ...` followed by
  `save()`, or with `queryset.update()`. The database does not prevent it.
- The default Django admin form would allow editing the status freely.
- The row lock serializes concurrent operations on the same ticket, so slow work inside
  the transaction would block other requests.
- The `TRANSITIONS` table and `docs/state-machine.md` must stay in sync.
- Changing the workflow requires a code change and a deployment.

### Mitigations

- `status` is read-only in the API serializers and in the admin site, where status
  changes are offered as admin actions that call `transition()`.
- The rule "status only changes through `transition()`" is documented and checked in
  code review. If bypasses ever become a real risk, a minimal trigger that only checks
  the `(from, to)` pair could be added as a second layer, without moving the side
  effects out of Python.
- Only database work happens inside the transaction. Notifications such as emails are
  scheduled with `transaction.on_commit()`, so they are sent only if the transition is
  committed.
- Any change to `TRANSITIONS` updates `docs/state-machine.md` and the parametrized
  tests in the same pull request.
- A fixed workflow is accepted: it matches the requirements of the project.
