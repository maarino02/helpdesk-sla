"""Settings for the test suite."""

from .base import *  # noqa: F403

DEBUG = False

SECRET_KEY = "test-only-not-secret"

# Hashing passwords with PBKDF2 is deliberately slow; tests create many users.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
