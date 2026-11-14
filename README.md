# HelpDesk SLA

Multi-tenant helpdesk with SLA tracking in business hours, built with Django,
PostgreSQL Row-Level Security and Celery.

> **Status:** in development. Currently in phase 1 (project skeleton). See the [roadmap](#roadmap).

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
- Background alerts when a ticket is about to breach its SLA
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
- [ ] **Phase 1 - Skeleton:** Django project, Docker Compose, tests and CI
- [ ] **Phase 2 - Data model:** models, constraints, indexes and migrations
- [ ] **Phase 3 - Auth & isolation:** JWT, roles and Row-Level Security policies
- [ ] **Phase 4 - Tickets API:** CRUD, state transitions, comments and event history
- [ ] **Phase 5 - SLA engine:** business-time calculation, holidays and pauses
- [ ] **Phase 6 - Background jobs:** SLA alerts and auto-close with Celery
- [ ] **Phase 7 - Demo data:** realistic seed data and demo accounts
- [ ] **Phase 8 - Analytics:** SQL dashboard queries, indexes and query plans
- [ ] **Phase 9 - Frontend:** ticket views and dashboard with Chart.js
- [ ] **Phase 10 - Release:** deployment, screenshots and live demo

## Getting started

Instructions to run the whole stack with `docker compose up` will be added in phase 1.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
