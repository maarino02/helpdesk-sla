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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
