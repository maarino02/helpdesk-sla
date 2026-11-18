from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("auth/token/", views.LoginView.as_view(), name="token"),
    path("auth/token/refresh/", views.RefreshView.as_view(), name="token-refresh"),
    path("me/", views.MeView.as_view(), name="me"),
]
