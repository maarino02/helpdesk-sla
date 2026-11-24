# HelpDesk SLA

[![CI](https://github.com/maarino02/helpdesk-sla/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/maarino02/helpdesk-sla/actions/workflows/ci.yml)

Multi-tenant helpdesk with SLA tracking in business hours, built with Django,
PostgreSQL Row-Level Security and Celery.

> **Status:** in development. Phase 6 (background jobs) is complete; phase 7 (demo data) is next. See the [roadmap](#roadmap).

## Overview

HelpDesk SLA is a support platform in the style of Zendesk or Jira Service Desk.
A support provider (agents and admins) handles incidents reported by several client
companies. Each client can only see its own tickets, and that isolation is enforced by
the database itself through PostgreSQL Row-Level Security, not only by application code.

Every ticket gets SLA deadlines for first response and resolution, measured in business
hours according to each client's support calendar, excluding weekends and holidays.
A background process watches those deadlines and raises alerts before they are breached.

## Planned features

- Multi-tenant data isolation with PostgreSQL Row-Level Security
- Three roles: client, agent and admin
- Tickets with category, priority (low, medium, high, critical) and assignee
- Validated state machine: new → open → pending → resolved → closed
- Public comments and internal notes visible only to agents
- SLA policies per priority, calculated in business hours (weekends and holidays excluded)
- Full audit trail in an append-only events table
- Background alerts when a ticket is about to breach its SLA, or has breached it
- Automatic closing of resolved tickets the client does not reopen within 7 days
- Email notifications to the requester and to the support team
- Analytics dashboard built on hand-written SQL: SLA compliance, response and resolution
  times, ticket aging, agent workload and category trends

## Tech stack

| Layer             | Technology                                          |
|-------------------|-----------------------------------------------------|
| Backend           | Python, Django 5.2 LTS, Django REST Framework, JWT  |
| Database          | PostgreSQL 17 with Row-Level Security               |
| Background jobs   | Celery, Redis                                       |
| Frontend          | HTML, CSS, vanilla JavaScript, Chart.js             |
| Testing & quality | pytest, ruff, pre-commit                            |
| Infrastructure    | Docker Compose, GitHub Actions                      |

## Roadmap

- [x] **Phase 0 - Design:** data model, state machine and architecture decisions
- [x] **Phase 1 - Skeleton:** Django project, Docker Compose, tests and CI
- [x] **Phase 2 - Data model:** models, constraints, indexes and migrations
- [x] **Phase 3 - Auth & isolation:** JWT, roles and Row-Level Security policies
- [x] **Phase 4 - Tickets API:** CRUD, state transitions, comments and event history
- [x] **Phase 5 - SLA engine:** business-time calculation, holidays and pauses
- [x] **Phase 6 - Background jobs:** SLA alerts, auto-close and email notifications with Celery
- [ ] **Phase 7 - Demo data:** realistic seed data and demo accounts
- [ ] **Phase 8 - Analytics:** SQL dashboard queries, indexes and query plans
- [ ] **Phase 9 - Frontend:** ticket views and dashboard with Chart.js
- [ ] **Phase 10 - Release:** deployment, screenshots and live demo

## Getting started

This section is the quick start. Everything else about running and extending the
project (every environment variable, the database roles, migrations, working with data
under Row-Level Security, the API, the two kinds of tests, a checklist for new tables
and troubleshooting) is in the **[development guide](docs/development.md)**.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose v2
- [uv](https://docs.astral.sh/uv/) (it installs the Python version in `.python-version`)
- GNU make. On Windows: `winget install ezwinports.make`

Every `make` target runs a single command, so without make you can copy it from the
[Makefile](Makefile).

Copy the example configuration first. Its values are for local development only;
set your own `JWT_SIGNING_KEY` (the file explains how to generate one):

```sh
cp .env.example .env
```

On first start, the database container creates two roles: `helpdesk_owner`, which owns
the tables and runs the migrations, and `helpdesk_app`, which the application uses and
which is always subject to Row-Level Security.

### Run the full stack in Docker

```sh
make up-app
```

This builds the production image and starts PostgreSQL, Redis, a one-off `migrate`
container and then the web server, the Celery worker and Celery beat. Migrations never
run automatically on web startup; the web, worker and beat services wait until `migrate`
has finished successfully.

Check that everything is up:

- <http://127.0.0.1:8000/health/live/>: the web process is running
- <http://127.0.0.1:8000/health/ready/>: the database and Redis are reachable

Stop it with `make down`. Data is kept in a named volume.

### Local development

Run Django and Celery on your machine against the database and Redis containers:

```sh
uv sync                      # create .venv with all dependencies
uv run pre-commit install    # run ruff before each commit
make up                      # start only PostgreSQL and Redis
make migrate                 # as helpdesk_owner
uv run backend/manage.py createsuperuser
make run                     # http://127.0.0.1:8000 (admin at /admin/)
```

If you need background tasks, start `make worker` and `make beat` in other terminals.
Do not run them while `make up-app` is running: two workers would share the queue and
two beat schedulers would send every periodic task twice.

### Common commands

| Command                 | Description                                              |
|-------------------------|----------------------------------------------------------|
| `make test`             | Run the test suite against PostgreSQL (needs `make up`)  |
| `make lint`             | Check linting and formatting with ruff, as CI does       |
| `make format`           | Apply ruff autofixes and format the code                 |
| `make migrations-check` | Fail if a model change has no migration                  |
| `make makemigrations`   | Create migrations for model changes                      |
| `make psql`             | Open a SQL console in the database container             |
| `make db-roles`         | Create or update the database roles (idempotent)         |
| `make ps` / `make logs` | Show container status / follow container logs            |
| `make down`             | Stop and remove all containers                           |

### API

A JSON API with JWT authentication: tickets, state transitions, triage, comments and
history. Clients see only their organization's tickets, and every status change goes
through the state machine. Endpoints, permissions by role, pagination and error codes are
in the [API reference](docs/api.md).

### Background jobs

Celery runs the periodic jobs: every minute, a set of `INSERT ... SELECT ... ON CONFLICT
DO NOTHING` statements records the SLA warnings and breaches that have become due; every
hour, resolved tickets older than 7 days are closed through the state machine. Status
changes and alerts send emails, enqueued only after the transaction commits. Schedules,
idempotency and delivery guarantees are in [Background jobs](docs/background-jobs.md).

## Documentation

- [Development guide](docs/development.md): configuration, database roles, working
  with Row-Level Security, tests, checklist for new tables, troubleshooting
- [API reference](docs/api.md): endpoints, permissions by role, pagination and errors
- [SLA and business time](docs/sla.md): what a business minute is, how deadlines are
  computed and how they move
- [Background jobs](docs/background-jobs.md): SLA alerts, auto-close and email
  notifications: schedules, idempotency and delivery guarantees
- [Data model](docs/data-model.md): ER diagram, constraints, indexes and RLS policies
- [Ticket state machine](docs/state-machine.md): states, transitions and permissions
- Architecture decision records:
  - [ADR-0001](docs/adr/0001-multitenancy-shared-schema-rls.md): multi-tenancy with a shared schema and RLS
  - [ADR-0002](docs/adr/0002-sla-business-time-stored-due-dates.md): SLA in business time with stored due dates
  - [ADR-0003](docs/adr/0003-ticket-state-machine-service-layer.md): ticket state machine in the service layer
  - [ADR-0004](docs/adr/0004-rls-request-context.md): row-level security context per request (identify, then widen)
  - [ADR-0005](docs/adr/0005-synchronous-deadline-recalculation.md): deadlines recalculated synchronously when a calendar changes

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
