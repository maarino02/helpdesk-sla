# Development guide

How to configure, run and extend the project day to day. The README has the quick
start; the reasons behind the design are in the [ADRs](adr/) and the
[data model](data-model.md).

- [Environment variables](#environment-variables)
- [Database roles](#database-roles)
- [Migrations](#migrations)
- [Working with data](#working-with-data)
- [Using the API](#using-the-api)
- [Tests](#tests)
- [Checklist for a new table](#checklist-for-a-new-table)
- [Troubleshooting](#troubleshooting)

## Environment variables

Copy `.env.example` to `.env`. Django and Docker Compose both read it; variables
already set in the environment take precedence. Locally, use `127.0.0.1` rather than
`localhost` in URLs: on Windows `localhost` tries IPv6 first and the connection waits
for it to time out.

| Variable | Used by | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django | Required in production; development has an insecure default. |
| `DJANGO_ALLOWED_HOSTS` | Django | Comma-separated. |
| `JWT_SIGNING_KEY` | Django | Signs API tokens. At least 32 characters, separate from `DJANGO_SECRET_KEY` so each can be rotated alone ([ADR-0004](adr/0004-rls-request-context.md)). Required in production and by Compose. |
| `DATABASE_URL` | Every process | Connects as `helpdesk_app`, the runtime role. |
| `DATABASE_OWNER_URL` | Migrations, tests and `seed_demo --reset` only | Connects as `helpdesk_owner`, which owns the tables. In Compose only the `migrate` service gets it. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `db` container | The superuser that creates the database and the roles. Never used by the application. |
| `HELPDESK_OWNER_PASSWORD`, `HELPDESK_APP_PASSWORD` | `docker/postgres/roles.sql` | Passwords of the two roles. Must match the two database URLs. |
| `REDIS_URL` | Celery, readiness check | Redis database 0: the broker. |
| `REDIS_CACHE_URL` | Django cache | Redis database 1. Kept apart from the broker because clearing the cache runs `FLUSHDB`. |
| `DRF_NUM_PROXIES` | Login throttling | Number of trusted proxies in front of the app. Default `0`: the client address is `REMOTE_ADDR`. Set it behind a proxy, or every client shares the proxy's limit. |
| `EMAIL_URL` | Notifications | Email backend as a URL, e.g. `smtp+tls://user:password@host:587`. Default `consolemail://`: emails are printed to the worker log. Required in production. |
| `DEFAULT_FROM_EMAIL` | Notifications | Sender of the notifications. Required in production. |
| `DEMO_PASSWORD` | `seed_demo` | Password of every demo account. Development defaults to `demo1234`; elsewhere the command refuses to run without it. |
| `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SECURE_HSTS_SECONDS`, `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | Production | HTTPS settings. HSTS starts at 0. |
| `WEB_CONCURRENCY` | Gunicorn | Worker processes in the image (default 2). |

Passwords inside database URLs must be percent-encoded if they contain characters such
as `@`, `:`, `/` or `?`.

## Database roles

| Role | Purpose |
|---|---|
| `helpdesk` (`POSTGRES_USER`) | Superuser of the container. Only creates the database and the roles. |
| `helpdesk_owner` | Owns the database, the tables and the policies. Runs the migrations and the test suite. `CREATEDB`, no `BYPASSRLS`. |
| `helpdesk_app` | Used by Django at runtime. Owns nothing, no `BYPASSRLS`; only the privileges granted in `core 0001`. |

`docker/postgres/roles.sql` creates them. It is idempotent: each run creates a missing
role and then sets every attribute and password again.

- **New data volume:** the `db` image runs the script once, on first start.
- **Existing volume:** `make db-roles` runs it again. Use it to rotate passwords after
  changing them in `.env` (update the database URLs too).
- **CI:** a `psql` step runs it on the runner before the tests.

Tables must belong to `helpdesk_owner`. Check it in `make psql`:

```sql
SELECT tableowner, count(*) FROM pg_tables WHERE schemaname = 'public' GROUP BY 1;
```

If they belong to `helpdesk` (migrations run as the superuser, for instance on a
volume created before the roles existed), recreate the volume: `REASSIGN OWNED BY
helpdesk` is refused, because the container's superuser also owns system objects.

```sh
make down
docker volume rm helpdesk-sla_pgdata
make up
make migrate
```

## Migrations

`make migrate` runs `manage.py migrate --database owner`, connected as
`helpdesk_owner`. A plain `manage.py migrate` connects as `helpdesk_app` and fails with
`permission denied for schema public`. That is intended: there is no database router,
because a router would skip the operations and still record the migrations as applied.

Row-level security is forced, so it applies to the owner too. A data migration that
reads or writes tenant rows must set the system context first:

```python
def forwards(apps, schema_editor):
    schema_editor.execute("SELECT set_config('app.role', 'system', true)")
    db = schema_editor.connection.alias
    Ticket = apps.get_model("tickets", "Ticket")
    for ticket in Ticket.objects.using(db).iterator():
        ...
```

Every query in a `RunPython` must go through `.using(schema_editor.connection.alias)`.
`migrate --database owner` runs on the owner's connection, while `Ticket.objects` alone
uses `default`, a different session as `helpdesk_app`: it would not have the system
context (it is per connection) and it would wait forever for the lock that the
migration's own `ALTER TABLE` holds. `test_migrations.py` reproduces this with a lock
timeout.

## Working with data

Every query runs with a row-level security context: `app.role`, `app.org_id`,
`app.user_id` and, during login, `app.login_email`. Without context the policies match
no rows, with no error. `apps/core/rls.py` is the only module that sets it.

| Where | Context |
|---|---|
| API requests | Set by `RlsJWTAuthentication` from the token's user, with the user's current role and organization. |
| Admin requests | Set by `RlsModelBackend.get_user` from the session; `AdminRlsMiddleware` keeps the view and the template rendering in one transaction. |
| Login | `login` context: reads only the row with that email, read-only. |
| Celery tasks, management commands | `with system_context():` sees every organization. It opens a durable transaction, so it cannot be used inside a request. |

### Django shell

`manage.py shell` sees nothing until you set a context:

```python
from apps.core.rls import system_context
from apps.accounts.models import User

User.objects.count()  # 0: no context

with system_context():
    User.objects.count()  # every user
```

`createsuperuser` and `changepassword` are overridden to run with `system_context()`.

Change business calendars (slots, holidays, time zone) and an organization's calendar
in the admin, not in the shell: saving there reprojects the SLA deadlines of the open
tickets that use them, in the same transaction
([ADR-0005](adr/0005-synchronous-deadline-recalculation.md)). A change made in the
shell or with SQL leaves those deadlines computed with the old calendar.

### Demo data

```sh
make seed                                    # empty database: demo clients, accounts, 6 months
make seed ARGS=--reset                       # delete everything first, users included
make seed ARGS="--tickets 300 --seed 2"      # fewer tickets, another random seed
make seed ARGS="--reset --bulk --clients 40 --months 24 --tickets 200000"  # volume
```

What it creates, the demo accounts, how the history is planned and written, and why
`--reset` keeps ids growing are in [Demo data](demo-data.md). Two practical points:

- The default 1,500 tickets go through the services and take about 2.5 minutes from
  Windows. Everything is one transaction, so other sessions see nothing until the end;
  the progress lines are the sign that it is working.
- After a volume seed, run `ANALYZE` in `make psql` before measuring queries: the
  application role cannot, and the planner has no statistics for the new rows yet.

### SQL console

`make psql` connects as the superuser, which bypasses row-level security. To see what
the application sees, switch role and set a context inside a transaction:

```sql
BEGIN;
SET LOCAL ROLE helpdesk_app;
SELECT set_config('app.role', 'client', true), set_config('app.org_id', '1', true);
SELECT id, title FROM ticket;     -- only organization 1
ROLLBACK;
```

## Using the API

The endpoints, permissions, pagination and error format are in the
[API reference](api.md). A quick check from the command line:

```sh
curl -s -X POST http://127.0.0.1:8000/api/auth/token/ \
     -d email=ana@acme.com -d password=secret
curl -s http://127.0.0.1:8000/api/me/ -H "Authorization: Bearer <access>"
```

In development (`DEBUG`), opening an endpoint in the browser shows Django REST
Framework's browsable API; it needs a token like any other client.

## Tests

`make test` needs `make up`. pytest-django creates `test_helpdesk` as
`helpdesk_owner`, runs the migrations and drops it at the end.

**Default tests** run inside one transaction that is rolled back at the end. An autouse
fixture (`backend/conftest.py`) switches to `helpdesk_app` with `SET LOCAL ROLE` and sets
`app.role = 'system'`, so tests exercise the real grants and can create rows of any
organization.

- To test as another role, call `set_context(...)` from `backend/tests/rls.py`. Values
  not given are cleared.
- `@pytest.mark.db_owner` keeps the owner, still with the system context.
- `@pytest.mark.django_db(transaction=True)` gets no fixture: no role switch and no
  context.

**End-to-end tests** use the `production_like` fixture (`backend/tests/conftest.py`):
no transaction around the test, connected as `helpdesk_app` at session level. Requests
commit for real and templates render after the view's transaction, as in production.
Create data with `system_context()`. Default tests hide problems that only appear after
a request commits, such as queries run while rendering a template; add an end-to-end
test for any view that renders templates or does work after returning.

Tests against the catalog keep the database honest:

| Test file | Checks |
|---|---|
| `test_schema.py` | Constraints and indexes match the models. |
| `test_privileges.py` | Table privileges of `helpdesk_app` match a matrix. |
| `test_rls_schema.py` | Every table is classified with or without RLS; `ENABLE`, `FORCE`, policies, column guards, views. |
| `test_isolation.py` | What each context sees in each table, on a two-organization dataset. |
| `test_api_views.py` | Every DRF view uses the RLS authentication class, and every API route is documented in `docs/api.md`. |

**Concurrency tests** (`backend/tests/test_*concurrency.py`, `test_ticket_numbering.py`,
`test_recalculation_locking.py`) run two or more real connections in threads, with
`production_like`. To prove that an operation takes a lock, another connection holds a
conflicting one and the operation runs with `SET LOCAL lock_timeout`; to force an order,
the test waits until `pg_locks` shows a lock request not yet granted (`pg_stat_activity`
hides other sessions from `helpdesk_app`). Choose the lock the other connection holds
with care: holding `FOR UPDATE` on a row also blocks a plain `UPDATE` of it, so the test
would pass without the explicit lock; `FOR KEY SHARE` conflicts only with `FOR UPDATE`.
Check every such test by removing the lock and watching it fail.

**Celery tasks** are tested by calling the function; the test settings use an
in-memory broker, so nothing reaches the development Redis. `on_commit` callbacks never
run inside the default test transaction: see
[Testing tasks](background-jobs.md#testing-tasks).

**Property-based tests** (`backend/apps/sla/tests/test_business_time_properties.py`) use
hypothesis: it generates calendars, instants and durations and checks properties that
must always hold (adding and measuring are inverse, measuring is additive, and so on).

- Profiles are registered in `backend/conftest.py`: `dev` (100 examples, the default) and
  `ci` (500, selected in CI with `HYPOTHESIS_PROFILE=ci`). Neither has a deadline per
  example. Run the CI profile locally with `HYPOTHESIS_PROFILE=ci make test`
  (PowerShell: `$env:HYPOTHESIS_PROFILE="ci"; make test`).
- When a property fails, hypothesis shrinks the input to a minimal case, and in CI it
  prints a `@reproduce_failure(...)` decorator that replays it exactly. Fix the code, then
  pin the case: a hand-written test with the expected value, and an `@example` on the
  property. The minimal case is otherwise only in `.hypothesis/`, a local folder that is
  not committed.
- Properties check that the functions agree with each other, not that the results are
  right: ignoring holidays everywhere would still pass them. The hand-written cases in
  `test_business_time.py` and `test_business_time_dst.py` check the values.

## Checklist for a new table

The catalog tests fail until each step is done, on purpose: every table needs an
explicit decision.

1. **Privileges.** In the migration that creates the table, grant `helpdesk_app` only
   what it needs (`GRANT SELECT, INSERT ... ON <table> TO helpdesk_app`). Never
   `TRUNCATE`, `REFERENCES` or `TRIGGER`. Add the table to `EXPECTED` in
   `test_privileges.py`.
2. **Row-level security or not.** Tenant data needs it. Add the table to
   `EXPECTED_POLICIES` or, with a reason, to `WITHOUT_RLS` in `test_rls_schema.py`.
3. **Policies.** `ENABLE` and `FORCE ROW LEVEL SECURITY`, policies `TO public` that fail
   closed (list the roles that grant access; anything else sees nothing). If the table
   has `organization_id` copied from a parent, protect it with a composite foreign key
   and check it in `WITH CHECK`.
4. **Column guard.** If non-admin roles may update some rows but not every column, add
   the `app_restrict_update(<allowed columns>)` trigger and list it in
   `EXPECTED_GUARDS`.
5. **Isolation matrix.** Add rows of both organizations to the dataset in
   `test_isolation.py` and the expected labels for every context.
6. **Views.** A view over tenant tables must be created `WITH (security_invoker = true)`.
   A materialized view has no row-level security at all and needs its own decision.
7. **Reverse SQL.** Every `RunSQL` gets a `reverse_sql`; check with
   `migrate --database owner <app> <previous migration>` and back.
8. **Demo reset.** Add the table to `TABLES` in `apps/demo/reset.py`, or
   `seed_demo --reset` fails because the table references one that is emptied.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| A query returns no rows, with no error | No row-level security context: shell without `system_context()`, a view that replaced the authentication classes, a template rendered outside the request transaction. |
| `permission denied for table ...` | `helpdesk_app` lacks a grant: add it in a migration and in `test_privileges.py`. |
| `permission denied for schema public` on `migrate` | Ran without `--database owner`. Use `make migrate`. |
| `new row violates row-level security policy` | The row written is outside what the context may see, for instance another organization's id. |
| `role ... may only change ... in ...` | The column guard: only admins and the system context change those columns. |
| `The row-level security context needs an open transaction` | `set_context` called in autocommit mode; wrap the code in `transaction.atomic()`. |
| `A durable atomic block cannot be nested` | `system_context()` used inside a request or another transaction. |
| 429 on login | Login throttling. Wait a minute, or locally clear the cache database: `docker compose exec redis redis-cli -n 1 FLUSHDB`. |
| No email after a status change or an alert | With the default `EMAIL_URL=consolemail://` emails are printed in the worker log: check that a worker is running. The requester is not told about their own changes, and only `pending`, `resolved` and `closed` are notified. |
| Compose fails with `required variable ... is missing` | A required variable is not in `.env`; compare it with `.env.example`. |
