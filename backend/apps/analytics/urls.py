from django.urls import path

from apps.analytics import views

app_name = "analytics"

urlpatterns = [
    path("analytics/sla-compliance/", views.SlaComplianceView.as_view(), name="sla-compliance"),
    path("analytics/response-times/", views.ResponseTimesView.as_view(), name="response-times"),
    path(
        "analytics/open-ticket-aging/",
        views.OpenTicketAgingView.as_view(),
        name="open-ticket-aging",
    ),
    path("analytics/agent-workload/", views.AgentWorkloadView.as_view(), name="agent-workload"),
]
