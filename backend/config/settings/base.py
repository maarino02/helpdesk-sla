"""Settings shared by every environment.

Environment-specific modules (dev, test, prod) import everything from here and
override what they need. Values that depend on the deployment are read from
environment variables; design decisions (UTC, bigint keys) are fixed in code.
"""

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
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local apps
    "apps.accounts",
    "apps.core",
    "apps.sla",
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
DATABASES = {
    "default": env.db("DATABASE_URL"),
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Redis
# Required, like DATABASE_URL: a missing value fails at startup, not on the first task.
REDIS_URL = env("REDIS_URL")

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
}

# Security
# Health checks come from inside the container network over plain HTTP, without
# the TLS proxy in between. When SECURE_SSL_REDIRECT is on (production), they
# would get a 301 and fail. Defined here so tests exercise the real value.
SECURE_REDIRECT_EXEMPT = [r"^health/"]

# Authentication
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

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
