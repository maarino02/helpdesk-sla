from django.urls import path

from apps.tickets import views

app_name = "tickets"

urlpatterns = [
    path("tickets/", views.TicketListCreateView.as_view(), name="list"),
    path("tickets/<int:pk>/", views.TicketDetailView.as_view(), name="detail"),
    path(
        "tickets/<int:pk>/transitions/",
        views.TicketTransitionView.as_view(),
        name="transitions",
    ),
    path(
        "tickets/<int:pk>/comments/",
        views.TicketCommentListCreateView.as_view(),
        name="comments",
    ),
    path("tickets/<int:pk>/events/", views.TicketEventListView.as_view(), name="events"),
    path("categories/", views.CategoryListView.as_view(), name="categories"),
]
