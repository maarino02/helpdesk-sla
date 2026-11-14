#!/bin/sh
# Starts one of the application's processes. One image runs every role:
#
#   web       Gunicorn on port 8000
#   worker    Celery worker
#   beat      Celery beat (run exactly one)
#   migrate   apply migrations, then exit (never done automatically on start)
#   anything else is executed as is, e.g. "python manage.py shell"
#
# Every branch uses exec: the server replaces this shell as PID 1, so it receives
# SIGTERM from "docker stop" directly and can shut down gracefully.
set -eu

role="${1:-web}"
[ "$#" -gt 0 ] && shift

case "$role" in
  web)
    # /dev/shm: workers write heartbeats to a temp file; on the container's
    # overlay filesystem that write can block and get workers killed.
    # The worker count comes from WEB_CONCURRENCY.
    exec gunicorn config.wsgi --bind 0.0.0.0:8000 --worker-tmp-dir /dev/shm \
      --access-logfile - "$@"
    ;;
  worker)
    exec celery -A config worker --loglevel=info "$@"
    ;;
  beat)
    # Beat stores the last run time of each task; the code directory is
    # read-only for the app user, /tmp is not.
    exec celery -A config beat --loglevel=info --schedule /tmp/celerybeat-schedule "$@"
    ;;
  migrate)
    exec python manage.py migrate --noinput "$@"
    ;;
  *)
    exec "$role" "$@"
    ;;
esac
