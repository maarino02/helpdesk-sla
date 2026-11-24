"""seed_demo --reset: TRUNCATE on the owner's connection, then the seed as the app.

With real commits: in a test transaction, the owner's TRUNCATE (another session)
would wait forever for the locks of the rows the seed inserted.
"""

import io

import pytest
from django.core.management import CommandError, call_command
from django.db import connections
from django.db.models import Max, Min

from apps.accounts.models import User
from apps.core.rls import system_context
from apps.demo import reset
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "owner"])


@pytest.fixture(autouse=True)
def _demo_password(settings):
    settings.DEMO_PASSWORD = "test-demo-password"


@pytest.fixture
def owner():
    yield connections["owner"]
    # Otherwise the session stays open and the test database cannot be dropped.
    connections["owner"].close()


def seed(**options):
    call_command("seed_demo", tickets=5, stdout=io.StringIO(), **options)


def user_ids():
    with system_context():
        return User.objects.aggregate(low=Min("pk"), high=Max("pk"))


def test_reset_replaces_the_data(production_like, owner):
    seed()
    with pytest.raises(CommandError, match="--reset"):
        seed()

    seed(reset=True)

    with system_context():
        assert Organization.objects.count() == 3


def test_reset_never_reuses_ids(production_like, owner):
    # A refresh token issued before the reset names its user by id: that id must
    # not belong to somebody else afterwards.
    seed()
    before = user_ids()

    seed(reset=True)

    assert user_ids()["low"] > before["high"]


def test_truncate_empties_every_listed_table(production_like, owner):
    seed()

    reset.truncate(owner)

    with system_context(), connections["default"].cursor() as cursor:
        for table in reset.TABLES:
            cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 (constant names)
            assert cursor.fetchone() == (0,), table
