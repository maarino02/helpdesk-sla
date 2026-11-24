from pathlib import Path

from django.conf import settings

from apps.core.tasks import ping
from config.celery import app


def test_ping_returns_pong():
    # Called as a plain function: tests the logic without a broker or worker.
    assert ping() == "pong"


def test_beat_schedule_only_references_registered_tasks():
    # A typo in a task name would otherwise only show up as an error in the
    # worker log when beat first sends it.
    app.loader.import_default_modules()

    for entry in app.conf.beat_schedule.values():
        assert entry["task"] in app.tasks


BACKGROUND_JOBS = Path(settings.BASE_DIR).parent / "docs" / "background-jobs.md"


def project_tasks():
    app.loader.import_default_modules()
    return {name for name in app.tasks if name.startswith("apps.")}


def test_every_task_is_documented():
    reference = BACKGROUND_JOBS.read_text(encoding="utf-8")
    tasks = project_tasks()

    # Guard: an empty set would make the check pass vacuously.
    assert "apps.tickets.tasks.raise_sla_alerts" in tasks
    # In backticks, as the overview table writes them.
    assert {name for name in tasks if f"`{name}`" not in reference} == set()
