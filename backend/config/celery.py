"""Celery application.

Run from the repository root (see the Makefile):
    celery --workdir backend -A config worker
"""

import os

from celery import Celery

# Same default as manage.py; containers set the variable explicitly.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("config")

# Every CELERY_* Django setting configures Celery (CELERY_BROKER_URL -> broker_url).
app.config_from_object("django.conf:settings", namespace="CELERY")

# Find tasks.py in every installed app.
app.autodiscover_tasks()
