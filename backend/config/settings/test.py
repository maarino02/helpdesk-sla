"""Settings for the test suite."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import DATABASES, env

# The test database is created and migrated as helpdesk_owner: the application
# role cannot create databases. Each test then switches to helpdesk_app with
# SET LOCAL ROLE (backend/conftest.py). Required here, unlike in the other
# environments. Only the credentials change; ATOMIC_REQUESTS and the rest of
# the options from base.py are kept.
DATABASES["default"] = {**DATABASES["default"], **env.db("DATABASE_OWNER_URL")}
# Same test database and credentials under the "owner" alias, so code that names
# it still works. Without MIRROR both aliases would try to create test_<name>.
# Built from the URL alone, as in base.py: a copy of "default" would carry
# ATOMIC_REQUESTS and open a transaction on this alias in every request.
DATABASES["owner"] = {**env.db("DATABASE_OWNER_URL"), "TEST": {"MIRROR": "default"}}

# The suite relies on PostgreSQL-only features (RLS, raw SQL constraints,
# SELECT ... FOR UPDATE). Passing tests on any other engine would prove nothing.
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("The test suite must run against PostgreSQL.")

DEBUG = False

SECRET_KEY = "test-only-not-secret"

# Hashing passwords with PBKDF2 is deliberately slow; tests create many users.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
