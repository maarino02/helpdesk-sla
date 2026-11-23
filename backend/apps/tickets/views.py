"""Ticket API views: they translate HTTP into selector and service calls."""

from django.urls import reverse
from rest_framework import generics, status
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsClient
from apps.tickets.models import Category
from apps.tickets.selectors import ticket_list, tickets_for
from apps.tickets.serializers import (
    CategorySerializer,
    TicketCreateSerializer,
    TicketFilterSerializer,
    TicketListSerializer,
    TicketSerializer,
)
from apps.tickets.services.creation import create_ticket


class TicketCursorPagination(CursorPagination):
    """Newest first. A cursor instead of page numbers: no COUNT(*) and no OFFSET,
    whose cost grows with the page number, and no repeated or skipped rows when
    tickets are created between two pages. id breaks ties in created_at.
    """

    page_size = 25
    ordering = ("-created_at", "-id")
    # No page_size_query_param: a client cannot ask for thousands of rows.


class TicketListCreateView(generics.ListCreateAPIView):
    """GET: the tickets the user may see, filtered. POST: a client opens a ticket."""

    pagination_class = TicketCursorPagination
    serializer_class = TicketListSerializer

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsClient()]
        return super().get_permissions()

    def get_queryset(self):
        filters = TicketFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return ticket_list(self.request.user, **filters.validated_data)

    def create(self, request, *args, **kwargs):
        serializer = TicketCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = create_ticket(requester=request.user, **serializer.validated_data)
        return Response(
            TicketSerializer(ticket).data,
            status=status.HTTP_201_CREATED,
            headers={"Location": reverse("tickets:detail", args=[ticket.pk])},
        )


class TicketDetailView(generics.RetrieveAPIView):
    """One ticket. Another organization's ticket is a 404, not a 403: a 403
    would confirm that the ticket exists.
    """

    serializer_class = TicketSerializer

    def get_queryset(self):
        return tickets_for(self.request.user)


class CategoryListView(generics.ListAPIView):
    """Active categories, to fill the creation form. A small catalog: no paging."""

    serializer_class = CategorySerializer
    pagination_class = None
    queryset = Category.objects.filter(is_active=True)
