"""Django's createsuperuser, run with the system context.

app_user has forced row-level security: without context the command could
neither check whether the email exists nor insert the user. Overrides the
command of django.contrib.auth because apps.accounts comes first in
INSTALLED_APPS.
"""

from django.contrib.auth.management.commands import createsuperuser

from apps.core.rls import system_context


class Command(createsuperuser.Command):
    def handle(self, *args, **options):
        with system_context():
            return super().handle(*args, **options)
