from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("auth/token/", views.LoginView.as_view(), name="token"),
    path("auth/token/refresh/", views.RefreshView.as_view(), name="token-refresh"),
    # For the browser (ADR-0006): the refresh token in a cookie, not in the body.
    path("auth/session/", views.SessionLoginView.as_view(), name="session"),
    path("auth/session/refresh/", views.SessionRefreshView.as_view(), name="session-refresh"),
    path("auth/session/logout/", views.SessionLogoutView.as_view(), name="session-logout"),
    path("me/", views.MeView.as_view(), name="me"),
]
