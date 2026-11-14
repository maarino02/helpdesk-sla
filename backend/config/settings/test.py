"""Settings for the test suite."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

# The suite relies on PostgreSQL-only features (RLS, raw SQL constraints,
# SELECT ... FOR UPDATE). Passing tests on any other engine would prove nothing.
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":  # noqa: F405
    raise ImproperlyConfigured("The test suite must run against PostgreSQL.")

DEBUG = False

SECRET_KEY = "test-only-not-secret"

# Hashing passwords with PBKDF2 is deliberately slow; tests create many users.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
