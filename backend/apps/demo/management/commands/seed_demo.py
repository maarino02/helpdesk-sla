"""Fill an empty database with the demo data (make seed)."""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from apps.demo import reset
from apps.demo.catalog import CLIENTS, STAFF_DOMAIN
from apps.demo.seed import DataAlreadyExists, seed_demo


class Command(BaseCommand):
    help = "Fill an empty database with demo clients, users and six months of tickets."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete every client, user and ticket first (needs DATABASE_OWNER_URL).",
        )
        parser.add_argument(
            "--tickets", type=int, default=1500, help="Tickets to create (default 1500)."
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=1,
            help="Random seed. The data also depends on the current time: history ends now.",
        )

    def handle(self, *args, **options):
        password = settings.DEMO_PASSWORD
        if not password:
            raise CommandError("Set DEMO_PASSWORD: every demo account uses it.")
        if options["tickets"] < 0:
            raise CommandError("--tickets cannot be negative.")

        if options["reset"]:
            if "owner" not in settings.DATABASES:
                raise CommandError("--reset needs DATABASE_OWNER_URL: only the owner can TRUNCATE.")
            # Its own connection, in autocommit: the TRUNCATE is committed before the
            # seed starts writing on the application connection.
            reset.truncate(connections["owner"])
            self.stdout.write("Deleted the existing data.")

        started = time.monotonic()
        try:
            summary = seed_demo(
                password=password,
                tickets=options["tickets"],
                seed=options["seed"],
                now=timezone.now(),
            )
        except DataAlreadyExists:
            raise CommandError(
                "The database already has data. Run with --reset to delete it first "
                "(it also deletes every user, including superusers)."
            ) from None
        elapsed = time.monotonic() - started

        catalog = summary.catalog
        statuses = ", ".join(
            f"{count} {status}" for status, count in sorted(summary.tickets_by_status.items())
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(catalog.organizations)} clients, {len(catalog.agents)} agents, "
                f"{sum(len(users) for users in catalog.clients.values())} client users and "
                f"{summary.tickets_by_status.total()} tickets ({statuses or 'none'}) "
                f"in {elapsed:.1f} s. Open SLA alerts: {summary.alerts}."
            )
        )
        self.stdout.write("Demo accounts (password from DEMO_PASSWORD):")
        self.stdout.write(f"  admin   admin@{STAFF_DOMAIN}")
        self.stdout.write(f"  agent   agente@{STAFF_DOMAIN}")
        for slug in CLIENTS:
            self.stdout.write(f"  client  cliente@{slug}.example")
