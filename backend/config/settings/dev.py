"""Local development settings."""

from .base import *  # noqa: F403
from .base import REST_FRAMEWORK, SIMPLE_JWT, env

DEBUG = True

# Insecure fallbacks so a fresh clone works; never used outside development.
SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-only-key")
# At least 32 bytes: PyJWT warns about shorter HMAC keys.
SIMPLE_JWT["SIGNING_KEY"] = env(
    "JWT_SIGNING_KEY", default="insecure-dev-only-jwt-signing-key-change-me"
)

# HTML pages to explore the API from the browser, only while developing.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    *REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"],
    "rest_framework.renderers.BrowsableAPIRenderer",
]

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# The development server is plain HTTP: a Secure cookie would never be sent back.
REFRESH_COOKIE_SECURE = False

# Accounts created by make seed (documented in the README).
DEMO_PASSWORD = env("DEMO_PASSWORD", default="demo1234")
