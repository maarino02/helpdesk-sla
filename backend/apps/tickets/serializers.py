"""Ticket API serializers: input and output kept apart.

Input serializers only validate the shape of the request; the view passes the
validated data to a service. Output serializers are read-only and list their
fields explicitly, so nothing new leaks when a model gains a field.
"""

from rest_framework import serializers

from apps.accounts.models import User
from apps.organizations.models import Organization
from apps.sla.models import Priority
from apps.tickets.models import Category, Status, Ticket
from apps.tickets.selectors import ASSIGNEE_ME, ASSIGNEE_NONE


class UserSummarySerializer(serializers.ModelSerializer):
    """Who a ticket involves: never the email or anything else of the user."""

    class Meta:
        model = User
        fields = ["id", "full_name"]
        read_only_fields = fields


class OrganizationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name"]
        read_only_fields = fields


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name"]
        read_only_fields = fields


class TicketListSerializer(serializers.ModelSerializer):
    """A ticket in the list: everything but the description, which can be long."""

    reference = serializers.CharField(read_only=True)
    organization = OrganizationSummarySerializer(read_only=True)
    category = CategorySerializer(read_only=True)
    requester = UserSummarySerializer(read_only=True)
    assignee = UserSummarySerializer(read_only=True, allow_null=True)

    class Meta:
        model = Ticket
        fields = [
            "id",
            "reference",
            "number",
            "title",
            "status",
            "priority",
            "organization",
            "category",
            "requester",
            "assignee",
            "first_response_due_at",
            "resolution_due_at",
            "first_response_at",
            "resolved_at",
            "closed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TicketSerializer(TicketListSerializer):
    """One ticket in full."""

    class Meta(TicketListSerializer.Meta):
        fields = [*TicketListSerializer.Meta.fields, "description"]
        read_only_fields = fields


class TicketCreateSerializer(serializers.Serializer):
    """What a client sends to open a ticket. Status, number and deadlines are not
    accepted: the creation service sets them.
    """

    title = serializers.CharField(max_length=Ticket._meta.get_field("title").max_length)
    description = serializers.CharField()
    # Inactive categories are rejected here, with a field error; the service
    # checks it again for callers other than the API.
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.filter(is_active=True))
    priority = serializers.ChoiceField(choices=Priority.choices)


class TicketFilterSerializer(serializers.Serializer):
    """Query parameters of the list. An invalid value is a 400, not ignored: the
    user would otherwise take unfiltered results for filtered ones.
    """

    status = serializers.ChoiceField(choices=Status.choices, required=False)
    priority = serializers.ChoiceField(choices=Priority.choices, required=False)
    assignee = serializers.CharField(required=False)

    def validate_assignee(self, value):
        if value in (ASSIGNEE_ME, ASSIGNEE_NONE):
            return value
        if value.isdigit():
            return int(value)
        raise serializers.ValidationError(
            f'Debe ser "{ASSIGNEE_ME}", "{ASSIGNEE_NONE}" o el id de un usuario.'
        )
