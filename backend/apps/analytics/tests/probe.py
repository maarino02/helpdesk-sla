"""A stand-in metric for testing the plumbing before the real queries exist.

PROBE_SQL counts the tickets of the period and echoes its parameters.
UNFILTERED_SQL counts them with no organization filter at all: what row-level
security alone lets the user see.
"""

from django.urls import include, path

from apps.analytics.views import MetricView

PROBE_SQL = """
SELECT %(org)s::bigint AS organization,
       %(start)s::timestamptz AS period_start,
       %(end)s::timestamptz AS period_end,
       count(*) AS tickets
FROM ticket
WHERE created_at >= %(start)s
  AND created_at < %(end)s
  AND (%(org)s::bigint IS NULL OR organization_id = %(org)s)
"""

# The same count with no organization filter at all: row-level security only.
UNFILTERED_SQL = """
SELECT count(*) AS tickets
FROM ticket
WHERE created_at >= %(start)s AND created_at < %(end)s
"""


class ProbeView(MetricView):
    query = "probe"


class UnfilteredView(MetricView):
    query = "unfiltered"


urlpatterns = [
    path("probe/", ProbeView.as_view()),
    path("unfiltered/", UnfilteredView.as_view()),
    # The real login, for the end-to-end tests.
    path("api/", include("apps.accounts.urls")),
]


def install(sql_dir, monkeypatch):
    """Write the probe queries to sql_dir and point the runner at it."""
    from apps.analytics import queries

    (sql_dir / "probe.sql").write_text(PROBE_SQL, encoding="utf-8")
    (sql_dir / "unfiltered.sql").write_text(UNFILTERED_SQL, encoding="utf-8")
    monkeypatch.setattr(queries, "SQL_DIR", sql_dir)
    queries.load.cache_clear()
