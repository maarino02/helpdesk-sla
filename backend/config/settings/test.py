"""Settings for the test suite."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env

# The test database is created, migrated and used as helpdesk_owner: the
# application role cannot create databases. Tests that check permissions or
# row-level security switch to helpdesk_app with SET LOCAL ROLE inside the test
# transaction. Required here, unlike in the other environments.
DATABASES = {
    "default": env.db("DATABASE_OWNER_URL"),
}
# Same test database and credentials under the "owner" alias, so code that names
# it still works. Without MIRROR both aliases would try to create test_<name>.
DATABASES["owner"] = {**DATABASES["default"], "TEST": {"MIRROR": "default"}}

# The suite relies on PostgreSQL-only features (RLS, raw SQL constraints,
# SELECT ... FOR UPDATE). Passing tests on any other engine would prove nothing.
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("The test suite must run against PostgreSQL.")

DEBUG = False

SECRET_KEY = "test-only-not-secret"

# Hashing passwords with PBKDF2 is deliberately slow; tests create many users.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
