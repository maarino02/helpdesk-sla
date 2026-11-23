from django.urls import path

from apps.tickets import views

app_name = "tickets"

urlpatterns = [
    path("tickets/", views.TicketListCreateView.as_view(), name="list"),
    path("tickets/<int:pk>/", views.TicketDetailView.as_view(), name="detail"),
    path("categories/", views.CategoryListView.as_view(), name="categories"),
]
