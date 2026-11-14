"""Local development settings."""

from .base import *  # noqa: F403
from .base import env

DEBUG = True

# Insecure fallback so a fresh clone works; never used outside development.
SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-only-key")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
