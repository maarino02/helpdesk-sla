"""Settings shared by every environment.

Environment-specific modules (dev, test, prod) import everything from here and
override what they need. Values that depend on the deployment are read from
environment variables; design decisions (UTC, bigint keys) are fixed in code.
"""

from datetime import timedelta
from pathlib import Path

import environ

# backend/ (holds manage.py) and the repository root (holds .env).
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ROOT_DIR = BASE_DIR.parent

env = environ.Env()

# Local convenience: load .env if present. Variables already defined in the
# environment (Docker, CI) take precedence, because read_env does not override them.
ENV_FILE = ROOT_DIR / ".env"
if ENV_FILE.is_file():
    env.read_env(ENV_FILE)

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    # Before django.contrib.auth: its createsuperuser and changepassword commands
    # are replaced by versions that run with the row-level security context.
    "apps.accounts",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # PostgreSQL-specific features: exclusion constraints, range fields, extensions.
    "django.contrib.postgres",
    # simplejwt needs no entry: without its blacklist app it has no models.
    "rest_framework",
    # Local apps
    "apps.core",
    "apps.sla",
    "apps.organizations",
    "apps.tickets",
    "apps.demo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves static files from the application itself, with no separate web
    # server. Right after SecurityMiddleware so static responses still get the
    # security headers, and before everything else to skip unneeded work.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Right after the lazy request.user exists: the admin's view and rendering
    # share one transaction, and so one row-level security context.
    "apps.core.middleware.AdminRlsMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
# Required in every environment and always PostgreSQL: there is no SQLite
# fallback, so a missing variable fails loudly instead of silently changing engine.
# Connects as helpdesk_app, which is always subject to row-level security.
DATABASES = {
    "default": env.db("DATABASE_URL"),
}
DATABASES["default"].update(
    # One transaction per request: the row-level security context is set with
    # set_config(..., true), which lasts only until the end of the transaction.
    # Without it every query would run in its own transaction and lose the context.
    ATOMIC_REQUESTS=True,
    # Reuse connections for 60 s instead of opening one per request. Safe because
    # the context never outlives its transaction. Health checks replace a
    # connection that dropped while idle before the request uses it.
    CONN_MAX_AGE=60,
    CONN_HEALTH_CHECKS=True,
)

# Connects as helpdesk_owner, which owns the tables. Used only to run migrations
# ("migrate --database owner"). Optional on purpose: only the process that migrates
# is given this password, so web and worker processes cannot alter tables or
# row-level security policies even if they are compromised.
# No database router: "migrate" on the default alias must fail (helpdesk_app
# cannot create tables), not skip the operations and still record the
# migrations as applied in the shared django_migrations table.
if env.str("DATABASE_OWNER_URL", default=""):
    DATABASES["owner"] = env.db("DATABASE_OWNER_URL")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Redis
# Required, like DATABASE_URL: a missing value fails at startup, not on the first task.
REDIS_URL = env("REDIS_URL")

# Cache (login throttling). Shared by every web process, unlike the default
# per-process memory cache. A different Redis database from the Celery broker:
# cache.clear() runs FLUSHDB and would also delete the queued tasks.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_CACHE_URL"),
    }
}

# Celery
CELERY_BROKER_URL = REDIS_URL
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# JSON only: pickle would let anyone who can write to Redis run code in the worker.
# It also forces tasks to receive ids instead of model instances.
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
# Acknowledge a message only after the task finishes: if the worker dies midway,
# the task runs again. Tasks must therefore be idempotent. Prefetching one message
# at a time keeps unacknowledged work from piling up in a single worker.
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# Tasks write their outcome to the database; nobody reads return values.
CELERY_TASK_IGNORE_RESULT = True
# Periodic tasks live in code so they are versioned and reviewed like the rest.
CELERY_BEAT_SCHEDULE = {
    "ping": {
        "task": "apps.core.tasks.ping",
        "schedule": 300.0,  # seconds
    },
    "raise-sla-alerts": {
        "task": "apps.tickets.tasks.raise_sla_alerts",
        "schedule": 60.0,
        # Each run catches up with everything due, so a run still queued when
        # the next one is sent is redundant: after a worker outage, discard the
        # backlog instead of running it N times.
        "options": {"expires": 60},
    },
    "auto-close-resolved": {
        "task": "apps.tickets.tasks.auto_close_resolved",
        "schedule": 3600.0,
        "options": {"expires": 3600},  # same reasoning as the SLA alerts
    },
}

# Email: EMAIL_URL as smtp+tls://user:password@host:587 in production (phase 10);
# consolemail:// prints to the worker log. Tests use locmem (pytest-django).
vars().update(env.email_url("EMAIL_URL", default="consolemail://"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="HelpDesk SLA <no-reply@localhost>")
# Without a timeout, an SMTP server that stops answering blocks the worker forever.
EMAIL_TIMEOUT = 10  # seconds

# Resolved tickets the client neither confirms nor reopens are closed after this
# much calendar time (not business time: it is the client's window to react).
AUTO_CLOSE_AFTER = timedelta(days=7)

# Password of every account created by seed_demo. No default outside development:
# the command refuses to run without it, so a public demo never gets a known one.
DEMO_PASSWORD = env("DEMO_PASSWORD", default="")

# Security
# Health checks come from inside the container network over plain HTTP, without
# the TLS proxy in between. When SECURE_SSL_REDIRECT is on (production), they
# would get a 301 and fail. Defined here so tests exercise the real value.
SECURE_REDIRECT_EXEMPT = [r"^health/"]

# Authentication
AUTH_USER_MODEL = "accounts.User"

# Replaces ModelBackend: looks users up in the login context and switches to the
# user's own context once authenticated (ADR-0004).
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.RlsModelBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# REST API (see ADR-0004). Deny by default: an endpoint that does not declare its
# permissions requires an authenticated user.
REST_FRAMEWORK = {
    # Sets the row-level security context of every DRF view: nothing to inherit,
    # so nothing to forget. backend/tests/test_api_views.py fails for a view that
    # replaces it without being on its allow-list.
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.RlsJWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # JSON only; dev.py adds the browsable API.
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_RATES": {"login": "10/minute"},
    # Domain exceptions raised by services become HTTP responses here.
    "EXCEPTION_HANDLER": "apps.core.exceptions.exception_handler",
    # Throttling identifies clients by address. With the default (None), DRF uses
    # the whole X-Forwarded-For header, which the client writes: a new value per
    # attempt would bypass the limit. 0 uses REMOTE_ADDR; behind a proxy, set it
    # to the number of proxies so the address the proxy saw is used.
    "NUM_PROXIES": env.int("DRF_NUM_PROXIES", default=0),
}

# The token carries only the user id; role and organization are read from the
# database on every request. No rotation or blacklist: the per-request lookup
# already rejects deactivated users.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": False,
    # It would write last_login in the read-only login context.
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    # Set by each environment from JWT_SIGNING_KEY, separate from SECRET_KEY so each
    # can be rotated alone. None here, not simplejwt's fallback to SECRET_KEY: an
    # environment that forgot it fails when signing instead of silently sharing keys.
    "SIGNING_KEY": None,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# Internationalization
# Timestamps are always stored and handled in UTC. Each client's time zone is only
# used to evaluate business hours (see ADR-0002).
LANGUAGE_CODE = "es"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
