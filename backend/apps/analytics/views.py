"""Dashboard metrics: one read-only endpoint per query in sql/.

The views only translate HTTP: read the period, run the query, return its rows.
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics.period import read_period
from apps.analytics.queries import run
from apps.sla.models import Priority


class MetricView(APIView):
    """GET ?start=YYYY-MM-DD&end=YYYY-MM-DD&organization=<id>.

    Subclasses name their query and, if needed, narrow the permissions. The
    response echoes the period actually used (defaults filled in, a client's own
    organization) next to the rows.
    """

    query: str
    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = read_period(request)
        rows = run(self.query, {**period.params(), **self.extra_params()})
        return Response({"period": period.describe(), "results": rows})

    def extra_params(self) -> dict:
        """Parameters of this query besides the period's."""
        return {}


class PerPriorityView(MetricView):
    """A metric per month and priority: the query gets the priorities to list."""

    def extra_params(self):
        # From the code, not repeated in the SQL: one list of priorities.
        return {"priorities": list(Priority.values)}


class SlaComplianceView(PerPriorityView):
    """First responses and resolutions within their deadline, per month and priority."""

    query = "sla_compliance"


class ResponseTimesView(PerPriorityView):
    """Median and p90 of the first response and resolution times, in business minutes."""

    query = "response_times"
