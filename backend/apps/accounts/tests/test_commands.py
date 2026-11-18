"""createsuperuser and changepassword, overridden to run with the system context."""

from io import StringIO

import pytest
from django.contrib.auth.management.commands import changepassword
from django.core.management import call_command

from apps.accounts.models import Role, User
from apps.accounts.tests.factories import AgentUserFactory
from apps.core import rls

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "a-long-enough-password-1"


def test_createsuperuser_works_with_row_level_security(monkeypatch):
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", NEW_PASSWORD)
    # As from a terminal: without the override the command could not even check
    # whether the email is taken.
    rls.clear_context()

    call_command(
        "createsuperuser",
        interactive=False,
        email="Root@Example.com",
        full_name="Root",
        stdout=StringIO(),
    )

    rls.set_context(role="system")
    user = User.objects.get(email="root@example.com")
    assert (user.role, user.is_staff, user.is_superuser) == (Role.ADMIN, True, True)
    assert user.check_password(NEW_PASSWORD)


def test_changepassword_works_with_row_level_security(monkeypatch):
    user = AgentUserFactory()
    # Answers both password prompts.
    monkeypatch.setattr(changepassword.Command, "_get_pass", lambda self, prompt="": NEW_PASSWORD)
    rls.clear_context()

    call_command("changepassword", user.email, stdout=StringIO())

    rls.set_context(role="system")
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
