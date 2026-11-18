# ADR-0004: Row-level security context per request: identify, then widen

- Status: Accepted
- Date: 2026-11-18

## Context

ADR-0001 isolates tenants with row-level security. Every policy reads the context
of the current transaction: `app.role`, `app.org_id`, `app.user_id` and, during
login, `app.login_email`. Without context the policies match no rows. The
application must therefore set the right context in every transaction, and three
facts make that harder than it looks:

- **Login happens before identity is known.** To check a password the application
  must read the user's row, but `app_user` is protected by the same policies.
- **Django REST Framework authenticates inside the view**, not in a middleware, and
  loads the user from the database while doing so.
- **Roles and memberships change.** An agent can be promoted, a client moved to
  another organization or deactivated, while their tokens are still valid.

The API uses JSON Web Tokens (`djangorestframework-simplejwt`); the Django admin
uses sessions. Celery tasks and management commands have no user at all.

## Decision

The context is built in three phases, each one allowed by its own policies
(`accounts 0004`). Every value is set with `set_config(name, value, true)`, local to
the transaction, and each request runs in one transaction (`ATOMIC_REQUESTS`).

1. **Login.** The login backend normalizes the email and sets `app.role = 'login'`
   and `app.login_email`. The policy `login_lookup` lets it read that one row and
   nothing else, and only read it: the database cannot know whether the password
   has been checked, so nothing is written in this context.
2. **Identify.** After the JWT signature (or the session) is verified, the request
   sets only `app.user_id`. The policy `own_row_select` lets it read the user's own
   row. Inactive users are rejected here.
3. **Widen.** `app.role` and `app.org_id` are taken from that row, as it is now, and
   set for the rest of the transaction. Writes such as `last_login` happen from this
   point on.

The JWT carries only the user id (`user_id`), plus the standard `exp`, `iat`, `jti`
and `token_type` claims. Role and organization always come from the database.

Background jobs and management commands set `app.role = 'system'`, which sees every
organization. It is never set from a request: the role always comes from the user's
row, and no request parameter can choose it.

Token settings:

- Access tokens live 15 minutes, refresh tokens 1 day. No rotation and no blacklist:
  logging out means the client discards its tokens.
- `HS256`, signed with `JWT_SIGNING_KEY`, a key separate from Django's `SECRET_KEY`.
- Sent in the `Authorization: Bearer` header, so the API needs no CSRF protection.
- `UPDATE_LAST_LOGIN` stays off in simplejwt: it would write during the read-only
  login phase. The login view updates `last_login` after widening the context.

Django REST Framework denies by default: `IsAuthenticated` is the default permission
and only the JSON renderer is enabled outside development. The authentication class
is a subclass of simplejwt's that sets `app.user_id` before loading the user; the
stock class would query `app_user` with no context and find nobody.

## Alternatives considered

### Role and organization as JWT claims

Setting the context straight from the token would save one query per request. But
the claims would be a copy of the user's row frozen at login: a demoted admin keeps
admin rights, and a deactivated or moved client keeps access, until the token
expires. Short lifetimes shrink that window without closing it, and a blacklist to
close it would be a lookup per request anyway. The identify phase does that lookup
and returns current data.

### A `SECURITY DEFINER` function for login

A function owned by `helpdesk_owner` could look up a user by email bypassing the
policies. It works, but it is a door around row-level security: whoever can call it
can enumerate users, and its correctness depends on code reviewed outside the
policies. The login policy keeps the rule in the same place as all the others.

### Context at session level (`SET` instead of `SET LOCAL`)

Connections are reused across requests (`CONN_MAX_AGE`). A session-level value would
survive into the next request on the same connection, possibly another user's. A
transaction-local value cannot leak; a test checks it.

### Refresh token rotation with a blacklist

simplejwt can rotate refresh tokens and store revoked ones (`token_blacklist`). It
adds two tables (to classify under row-level security), a cleanup task, and a write
on every refresh. Its main benefit, cutting off a user immediately, is already given
by the identify phase, which checks `is_active` on every request.

### Signing with `SECRET_KEY`

simplejwt's default. With a separate key, either one can be rotated after a leak
without invalidating the other: all API tokens without the admin sessions, or the
other way round.

## Consequences

### Positive

- A change of role, organization or active status applies to the next request, not
  when the token expires.
- Tokens carry no authorization data, so there is nothing in them to go stale.
- Every phase is enforced by the database: a bug in the application cannot read more
  than the current phase allows.

### Negative and risks

- One extra query per request (the user's own row), plus the `set_config` calls.
- A view that skipped the shared base view would run without context and see no
  data. Safe, but a bug, found by the first test of that endpoint.
- A stolen refresh token is valid for up to one day, unless the user is deactivated.
- Where the frontend stores the tokens (memory or an `httpOnly` cookie) is decided
  with the frontend; a cookie would bring CSRF protection back into scope.
