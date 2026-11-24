"""Production settings.

Every security-relevant value is required: a missing variable stops the process
at startup instead of running with an unsafe default.
"""

from .base import *  # noqa: F403
from .base import SIMPLE_JWT, env

DEBUG = False

SECRET_KEY = env("DJANGO_SECRET_KEY")
# Separate from SECRET_KEY (ADR-0004). Generate with secrets.token_urlsafe(50).
SIMPLE_JWT["SIGNING_KEY"] = env("JWT_SIGNING_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
# Required: with the base default, production emails would only reach the log.
vars().update(env.email_url("EMAIL_URL"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL")

# TLS is terminated by the platform's proxy, which forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Start with 0 and raise it once HTTPS is confirmed to work: browsers cache HSTS
# and would refuse plain HTTP for the whole period.
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)

# Static files: compressed copies plus a content hash in each file name
# (base.css -> base.6f2a1c.css), so browsers can cache them forever and still
# pick up changes. Requires collectstatic, which runs when building the image;
# kept out of dev and test so they work without it.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
