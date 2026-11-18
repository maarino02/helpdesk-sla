"""Row-level security context of the current transaction (ADR-0004).

The only module that calls set_config. Every value is local to the transaction
(set_config(..., true)), so it can never leak to the next request on a reused
connection. The policies read it back with app_role(), app_org_id(),
app_user_id() and app_login_email(), which treat '' as missing.

Requests build the context in three phases:

1. login_context(email): read the row with that email, and only read it.
2. activate_user(user_id), first half: user_id alone reads the user's own row.
3. activate_user(user_id), second half: role and organization from that row.

Background jobs and management commands use system_context() instead.
"""

from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.db import connection, transaction

SET_CONTEXT_SQL = """
SELECT set_config('app.role', %s, true),
       set_config('app.org_id', %s, true),
       set_config('app.user_id', %s, true),
       set_config('app.login_email', %s, true)
"""


def set_context(*, role=None, org_id=None, user_id=None, login_email=None):
    """Replace the whole context in one query.

    Values not given are set to '', so nothing from an earlier phase survives:
    activating a user, for instance, also clears app.login_email.
    """
    if not connection.in_atomic_block:
        # In autocommit mode the values would end with this very statement and
        # the next query would silently see nothing. Fail loudly instead.
        raise RuntimeError("The row-level security context needs an open transaction.")
    values = [role, org_id, user_id, login_email]
    with connection.cursor() as cursor:
        cursor.execute(SET_CONTEXT_SQL, ["" if value is None else str(value) for value in values])


def clear_context():
    """No context: the policies match no rows."""
    set_context()


def login_context(email):
    """Phase 1: let the login backend read the row with this email, read-only."""
    user_model = get_user_model()
    set_context(role="login", login_email=user_model.objects.normalize_email(email))


def activate_user(user_id):
    """Phases 2 and 3: identify the user, then widen to their current role.

    Returns the user, or None (and no context) if the id is invalid, unknown or
    belongs to an inactive user. Role and organization always come from the row
    as it is now, never from the token or the session.
    """
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        # app_user_id() casts to bigint: a malformed id would make every query fail.
        clear_context()
        return None

    set_context(user_id=user_id)
    user = get_user_model()._default_manager.filter(pk=user_id).first()
    if user is None or not user.is_active:
        clear_context()
        return None

    set_context(role=user.role, org_id=user.organization_id, user_id=user.pk)
    return user


@contextmanager
def system_context():
    """Every organization, for Celery tasks and management commands only.

    durable=True: Django refuses to open this block inside another transaction.
    Every request runs inside one (ATOMIC_REQUESTS), so a view cannot switch to
    the system context. The transaction Django opens around each test is exempt,
    so tests can still use it. On exit the work is committed.
    """
    with transaction.atomic(durable=True):
        set_context(role="system")
        yield
