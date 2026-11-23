"""Ticket API views: they translate HTTP into selector and service calls."""

from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import generics, status
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsClient, IsStaff
from apps.tickets.models import Category
from apps.tickets.selectors import comments_for, events_for, ticket_list, tickets_for
from apps.tickets.serializers import (
    CategorySerializer,
    CommentCreateSerializer,
    CommentSerializer,
    EventDisplay,
    EventSerializer,
    TicketCreateSerializer,
    TicketFilterSerializer,
    TicketListSerializer,
    TicketSerializer,
    TicketUpdateSerializer,
    TransitionSerializer,
)
from apps.tickets.services.comments import add_comment
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


class ChronologicalPagination(CursorPagination):
    """Oldest first, as a conversation or a history is read. Bounded pages, so
    a long-lived ticket never returns everything at once.
    """

    page_size = 50
    ordering = ("created_at", "id")


class TicketNestedMixin:
    """For views under /tickets/{pk}/: the ticket, looked up among the ones the
    user may see (404 otherwise), once per request.
    """

    def get_ticket(self):
        if not hasattr(self, "_ticket"):
            self._ticket = get_object_or_404(tickets_for(self.request.user), pk=self.kwargs["pk"])
        return self._ticket


class TicketCommentListCreateView(TicketNestedMixin, generics.ListCreateAPIView):
    """GET: the conversation (clients never see internal notes). POST: add a
    comment; the service applies its effects (first response, new -> open,
    pending -> open) and decides who may write what (403, 409).
    """

    serializer_class = CommentSerializer
    pagination_class = ChronologicalPagination

    def get_queryset(self):
        return comments_for(self.request.user, self.get_ticket())

    def create(self, request, *args, **kwargs):
        ticket = self.get_ticket()
        serializer = CommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = add_comment(ticket, request.user, **serializer.validated_data)
        # Only the comment: it is the resource created. A comment may also have
        # moved the ticket; the interface reads the ticket again if it needs it.
        return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)


class TicketEventListView(TicketNestedMixin, generics.ListAPIView):
    """The ticket history, with readable values for ids and codes."""

    serializer_class = EventSerializer
    pagination_class = ChronologicalPagination

    def get_queryset(self):
        return events_for(self.request.user, self.get_ticket())

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        serializer = EventSerializer(
            page,
            many=True,
            context={**self.get_serializer_context(), "display": EventDisplay(page)},
        )
        return self.get_paginated_response(serializer.data)
