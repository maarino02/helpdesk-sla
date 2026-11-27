from django.urls import path

from apps.organizations import views

app_name = "organizations"

urlpatterns = [
    path("organizations/", views.OrganizationListView.as_view(), name="list"),
]
