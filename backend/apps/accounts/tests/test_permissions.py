from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser

from apps.accounts.permissions import IsAdmin, IsClient, IsStaff
from apps.accounts.tests.factories import (
    AdminUserFactory,
    AgentUserFactory,
    ClientUserFactory,
)

USERS = {
    "client": ClientUserFactory.build,
    "agent": AgentUserFactory.build,
    "admin": AdminUserFactory.build,
    "anonymous": AnonymousUser,
}

EXPECTED = {
    IsClient: {"client"},
    IsStaff: {"agent", "admin"},
    IsAdmin: {"admin"},
}


@pytest.mark.parametrize("permission", EXPECTED, ids=lambda p: p.__name__)
@pytest.mark.parametrize("user", USERS)
def test_role_permissions(permission, user):
    request = SimpleNamespace(user=USERS[user]())

    assert permission().has_permission(request, view=None) is (user in EXPECTED[permission])
