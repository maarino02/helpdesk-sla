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
