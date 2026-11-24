# Shortcuts for common tasks.
#
# Each recipe is a single docker compose or uv command, written the same way in
# PowerShell, cmd and sh, so this file works on Windows, macOS, Linux and in CI.
# Keep shell logic (conditionals, loops, rm...) out of the recipes.

.PHONY: up up-app down ps logs psql db-roles run migrate makemigrations migrations-check seed test lint format worker beat

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
