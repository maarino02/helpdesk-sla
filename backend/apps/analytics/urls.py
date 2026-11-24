from django.urls import path

from apps.analytics import views

app_name = "analytics"

urlpatterns = [
    path("analytics/sla-compliance/", views.SlaComplianceView.as_view(), name="sla-compliance"),
]
