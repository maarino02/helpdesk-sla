# Shortcuts for common tasks.
#
# Each recipe is a single docker compose or uv command, written the same way in
# PowerShell, cmd and sh, so this file works on Windows, macOS, Linux and in CI.
# Keep shell logic (conditionals, loops, rm...) out of the recipes.

.PHONY: up up-app down ps logs psql db-roles run migrate makemigrations migrations-check seed bench-db bench-create bench-migrate bench-seed bench-analyze bench-drop bench-psql bench-run bench-queries test lint format worker beat

# Start the services in the background and wait until their healthchecks pass.
up:
	docker compose up -d --wait

# Full stack with the production image (migrate, web, worker, beat). Rebuilds the
# image if the code changed. Do not use together with run/worker/beat on the host.
up-app:
	docker compose --profile app up -d --build --wait

# Stop and remove the containers, including the app profile. Data is kept in the
# named volume.
down:
	docker compose --profile app down

ps:
	docker compose ps

logs:
	docker compose logs -f

# Interactive SQL console. User and database come from PGUSER and PGDATABASE.
psql:
	docker compose exec db psql

# Create or update the database roles (helpdesk_owner, helpdesk_app). Runs on its
# own when the volume is created; safe to repeat, for example to rotate passwords.
db-roles:
	docker compose exec db psql -f /docker-entrypoint-initdb.d/10-roles.sql

# Django development server on the host, against the containers.
run:
	uv run backend/manage.py runserver

# As helpdesk_owner (DATABASE_OWNER_URL): the application role cannot create tables.
migrate:
	uv run backend/manage.py migrate --database owner

makemigrations:
	uv run backend/manage.py makemigrations

# Fail if a model change has no migration yet. Writes nothing; CI runs it too.
migrations-check:
	uv run backend/manage.py makemigrations --check --dry-run

# Demo data on an empty database. To delete the current data first (every client,
# ticket and user, superusers included): make seed ARGS=--reset
seed:
	uv run backend/manage.py seed_demo $(ARGS)

# Measurement database (helpdesk_bench): the volume seed for query plans, apart
# from the development data. make bench-db runs the four steps below in order; to
# build it again: make bench-drop bench-db. Settings: config/settings/bench.py.
bench-db: bench-create bench-migrate bench-seed bench-analyze

bench-create:
	docker compose exec db createdb --owner=helpdesk_owner helpdesk_bench

bench-migrate:
	uv run backend/manage.py migrate --database owner --settings=config.settings.bench

bench-seed:
	uv run backend/manage.py seed_demo --bulk --clients 40 --months 24 --tickets 200000 --settings=config.settings.bench

# The planner has no statistics after a bulk load until autovacuum runs, and the
# application role cannot ANALYZE: the superuser does it here.
bench-analyze:
	docker compose exec db psql --dbname=helpdesk_bench --command="ANALYZE"

bench-drop:
	docker compose exec db dropdb --if-exists helpdesk_bench

bench-psql:
	docker compose exec db psql --dbname=helpdesk_bench

# The API against the measurement database, to time real requests.
bench-run:
	uv run backend/manage.py runserver --settings=config.settings.bench

# EXPLAIN ANALYZE of the dashboard queries on it, rolled back; ARGS="--setup file.sql"
# tries an index or a setting first (scripts/bench_queries.py).
bench-queries:
	uv run python scripts/bench_queries.py $(ARGS)

# Test suite. Needs the db container running (make up); pytest-django creates
# and drops a separate test_<name> database.
test:
	uv run pytest

# Check only, never modify files: this is what CI runs.
lint:
	uv run ruff check .
	uv run ruff format --check .

# Apply safe autofixes (import order, modern syntax...) and format the code.
format:
	uv run ruff check --fix .
	uv run ruff format .

# Celery worker on the host. Windows does not support the default prefork pool,
# so it runs with the single-process solo pool; containers use prefork.
worker:
	uv run celery --workdir backend -A config worker --pool=solo --loglevel=info

# Periodic task scheduler. Run only one beat at a time, or tasks are sent twice.
beat:
	uv run celery --workdir backend -A config beat --loglevel=info
