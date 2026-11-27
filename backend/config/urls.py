from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.organizations.urls")),
    path("api/", include("apps.tickets.urls")),
    path("api/", include("apps.analytics.urls")),
    path("", include("apps.core.urls")),
    path("", include("apps.web.urls")),
]
