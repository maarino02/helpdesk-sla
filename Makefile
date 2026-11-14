# Shortcuts for common tasks.
#
# Each recipe is a single docker compose or uv command, written the same way in
# PowerShell, cmd and sh, so this file works on Windows, macOS, Linux and in CI.
# Keep shell logic (conditionals, loops, rm...) out of the recipes.

.PHONY: up down ps logs psql run migrate makemigrations

# Start the services in the background and wait until their healthchecks pass.
up:
	docker compose up -d --wait

# Stop and remove the containers. Data is kept in the named volume.
down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f

# Interactive SQL console. User and database come from PGUSER and PGDATABASE.
psql:
	docker compose exec db psql

# Django development server on the host, against the containers.
run:
	uv run backend/manage.py runserver

migrate:
	uv run backend/manage.py migrate

makemigrations:
	uv run backend/manage.py makemigrations
