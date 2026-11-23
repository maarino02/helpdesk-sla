"""Ticket API views: they translate HTTP into selector and service calls."""

from django.urls import reverse
from rest_framework import generics, status
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsClient, IsStaff
from apps.tickets.models import Category
from apps.tickets.selectors import ticket_list, tickets_for
from apps.tickets.serializers import (
    CategorySerializer,
    TicketCreateSerializer,
    TicketFilterSerializer,
    TicketListSerializer,
    TicketSerializer,
    TicketUpdateSerializer,
    TransitionSerializer,
)
from apps.tickets.services.creation import create_ticket
from apps.tickets.services.state_machine import transition
from apps.tickets.services.triage import update_ticket


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


class TicketDetailView(generics.RetrieveUpdateAPIView):
    """GET: one ticket. PATCH: staff change the assignee, priority or category.

    Another organization's ticket is a 404, not a 403: a 403 would confirm that
    the ticket exists. No PUT: replacing the whole ticket makes no sense when only
    three fields are editable.
    """

    serializer_class = TicketSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAuthenticated(), IsStaff()]
        return super().get_permissions()

    def get_queryset(self):
        return tickets_for(self.request.user)

    def partial_update(self, request, *args, **kwargs):
        ticket = self.get_object()
        serializer = TicketUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Only the fields sent: an omitted field is not changed (UNSET in the
        # service), while "assignee": null unassigns.
        update_ticket(ticket, request.user, **serializer.validated_data)
        updated = self.get_queryset().get(pk=ticket.pk)
        return Response(TicketSerializer(updated).data)


class TicketTransitionView(generics.GenericAPIView):
    """POST {"to_status": ...}: move a ticket through the state machine.

    Only authentication is required here: which roles may perform which
    transition is decided by the state machine (403 and 409 come from it,
    through the exception handler). Responds with the updated ticket.
    """

    serializer_class = TransitionSerializer

    def get_queryset(self):
        return tickets_for(self.request.user)

    def post(self, request, *args, **kwargs):
        ticket = self.get_object()  # 404 for tickets the user cannot see.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transition(ticket, serializer.validated_data["to_status"], request.user)
        # Read again through the selector: one query with every relation the
        # response shows, instead of one lazy query per relation.
        updated = self.get_queryset().get(pk=ticket.pk)
        return Response(TicketSerializer(updated).data)


class CategoryListView(generics.ListAPIView):
    """Active categories, to fill the creation form. A small catalog: no paging."""

    serializer_class = CategorySerializer
    pagination_class = None
    queryset = Category.objects.filter(is_active=True)
