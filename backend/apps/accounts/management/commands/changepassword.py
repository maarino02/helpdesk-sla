"""Django's changepassword, run with the system context (see createsuperuser.py)."""

from django.contrib.auth.management.commands import changepassword

from apps.core.rls import system_context


class Command(changepassword.Command):
    def handle(self, *args, **options):
        with system_context():
            return super().handle(*args, **options)
