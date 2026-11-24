"""Fill an empty database with the demo data (make seed)."""

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from apps.core.rls import system_context
from apps.demo import reset
from apps.demo.catalog import CLIENTS, STAFF_DOMAIN, create_catalog


class Command(BaseCommand):
    help = "Fill an empty database with demo clients, users and SLA settings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete every client, user and ticket first (needs DATABASE_OWNER_URL).",
        )

    def handle(self, *args, **options):
        password = settings.DEMO_PASSWORD
        if not password:
            raise CommandError("Set DEMO_PASSWORD: every demo account uses it.")

        if options["reset"]:
            if "owner" not in settings.DATABASES:
                raise CommandError("--reset needs DATABASE_OWNER_URL: only the owner can TRUNCATE.")
            # Its own connection, in autocommit: the TRUNCATE is committed before the
            # seed starts writing on the application connection.
            reset.truncate(connections["owner"])
            self.stdout.write("Deleted the existing data.")

        today = timezone.now().year
        # One transaction: a failure leaves the database as it was.
        with system_context():
            if reset.has_business_data():
                raise CommandError(
                    "The database already has data. Run with --reset to delete it first "
                    "(it also deletes every user, including superusers)."
                )
            catalog = create_catalog(
                password_hash=make_password(password),
                # The history goes back months and the demo keeps running for a while.
                years=range(today - 1, today + 2),
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(catalog.organizations)} clients, {len(catalog.agents)} agents "
                f"and {sum(len(users) for users in catalog.clients.values())} client users."
            )
        )
        self.stdout.write("Demo accounts (password from DEMO_PASSWORD):")
        self.stdout.write(f"  admin   admin@{STAFF_DOMAIN}")
        self.stdout.write(f"  agent   agente@{STAFF_DOMAIN}")
        for slug in CLIENTS:
            self.stdout.write(f"  client  cliente@{slug}.example")
