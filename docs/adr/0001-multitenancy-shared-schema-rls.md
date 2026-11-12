# ADR-0001: Multi-tenancy with a shared schema and PostgreSQL Row-Level Security

- Status: Accepted
- Date: 2026-11-12

## Context

HelpDesk SLA is used by a support provider to handle incidents reported by several
client companies (tenants). A client must never see tickets, comments or users that
belong to another client. A leak would expose confidential incident details and, in a
real product, would end the business relationship.

At the same time, part of the system must work across all tenants. Agents handle
tickets from every client, and the analytics dashboard computes metrics such as SLA
compliance per client and per month, which require queries over all tenants' data.

The project also has practical constraints:

- It is built and maintained by a single developer, so operational complexity must
  stay low.
- The stack is Django and Django REST Framework on PostgreSQL.
- It will run on a managed platform with a single managed PostgreSQL instance.

## Decision

All tenants share one database and one schema. Every tenant-owned table has an
`organization_id` column, and PostgreSQL Row-Level Security (RLS) policies restrict
which rows each request can read or write.

The main elements are:

- **Two database roles.** `helpdesk_owner` owns the tables and runs migrations.
  `helpdesk_app` is used by the application at runtime: it does not own the tables and
  does not have `BYPASSRLS`. `FORCE ROW LEVEL SECURITY` is enabled on tenant tables so
  that even the owner is subject to the policies.
- **Context per transaction.** After the JWT is validated, the application runs
  `set_config('app.org_id', <id>, true)` and `set_config('app.role', <role>, true)`
  at the start of the request transaction. The third argument makes the values local
  to the transaction: they are discarded on commit or rollback and cannot leak into
  the next request that reuses the same pooled connection.
- **Fail closed.** Policies read the context with
  `NULLIF(current_setting('app.org_id', true), '')::bigint`. If no context was set,
  the expression is `NULL`, no comparison matches and the request sees no tenant rows
  instead of all of them. `NULLIF` is needed because, once a custom setting has been
  used in a session, PostgreSQL returns an empty string rather than `NULL` when it is
  not set.
- **Staff access.** When `app.role` is `agent` or `admin`, policies allow every row.
  Clients only see rows of their own organization, and internal comments are also
  hidden from them at database level. Background jobs set an explicit system context.

## Alternatives considered

### Application-level filtering only

Every query filters by organization, for example through a custom model manager. It is
the simplest option and uses no database-specific feature.

Rejected because isolation would depend on every query remembering the filter,
including raw SQL in the dashboard, admin actions and background jobs. A single omission
leaks data silently, and no test can prove that such an omission does not exist. This
filtering is kept, but as the first layer of protection rather than the only one.

### Schema per tenant

Each client gets its own PostgreSQL schema, typically with `django-tenants`. It offers
strong isolation and makes per-client backup and restore easy.

Rejected because migrations must run once per schema, onboarding a client means
creating a schema, and cross-tenant queries need a `UNION` over every schema or dynamic
SQL. That would make the analytics dashboard, one of the core goals of the project,
much harder to build. The overhead is not justified for a small number of tenants with
an identical structure.

### Database per tenant

Each client gets its own database. It provides the strongest isolation and independent
scaling, which suits large enterprise customers with regulatory requirements.

Rejected because one managed database per client multiplies cost and operations,
requires routing connections per request, and turns cross-tenant analytics into a
separate data pipeline. It is not feasible for this project.

## Consequences

### Positive

- Isolation is enforced by the database on every access path: ORM queries, raw SQL,
  the admin and background jobs. A missing filter no longer leaks data.
- Cross-tenant work stays simple. Agents and the dashboard use ordinary SQL over one
  schema, and there is a single migration path.
- Isolation can be demonstrated with tests that run raw SQL as the application role.

### Negative and risks

- Two database roles and two connection URLs are needed (`DATABASE_URL` for the
  application, `DATABASE_OWNER_URL` for migrations), plus SQL migrations for policies
  and grants that Django does not generate.
- Django REST Framework authenticates inside the view, not in a middleware, so the
  context must be set in a shared base view after authentication. An endpoint that
  skipped it would see no data: safe, but a bug.
- The context must be set inside a transaction. A session-level `SET` would leak
  between requests that share a pooled connection.
- Tests that connect as the owner or as a superuser bypass RLS and give false
  confidence. Superusers bypass RLS even when it is forced.
- Every query on a tenant table gets an extra predicate, so `organization_id` must be
  well indexed.
- The managed PostgreSQL provider must allow creating roles.
- The design depends on PostgreSQL-specific features.

### Mitigations

- Defense in depth: the application keeps filtering by organization in its querysets,
  and RLS catches anything that slips through.
- The context is set in one place, a common base view, so it cannot be forgotten in
  individual endpoints.
- Dedicated isolation tests connect as `helpdesk_app` and check, including with raw
  SQL, that a client cannot read another tenant's rows or internal comments.
- Indexes on tenant tables lead with `organization_id`, and their use will be verified
  with `EXPLAIN ANALYZE` against realistic data.
- Support for `CREATE ROLE` will be confirmed with the chosen provider before
  implementing RLS.
- The dependency on PostgreSQL is accepted: it is a deliberate choice of the stack.
