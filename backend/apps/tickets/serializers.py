"""Ticket API serializers: input and output kept apart.

Input serializers only validate the shape of the request; the view passes the
validated data to a service. Output serializers are read-only and list their
fields explicitly, so nothing new leaks when a model gains a field.
"""

from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import Role, User
from apps.organizations.models import Organization
from apps.sla.models import Priority
from apps.tickets.models import Category, EventType, Status, Ticket, TicketComment, TicketEvent
from apps.tickets.selectors import ASSIGNEE_ME, ASSIGNEE_NONE
from apps.tickets.services.deadlines import sla_state
from apps.tickets.services.state_machine import allowed_transitions


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
    """A ticket in the list: everything but the description, which can be long.

    sla_state is judged at one instant for the whole response, taken the first
    time it is needed and kept in the context (shared by every ticket of a list).
    """

    reference = serializers.CharField(read_only=True)
    organization = OrganizationSummarySerializer(read_only=True)
    category = CategorySerializer(read_only=True)
    requester = UserSummarySerializer(read_only=True)
    assignee = UserSummarySerializer(read_only=True, allow_null=True)
    sla_state = serializers.SerializerMethodField()

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
            "sla_state",
            "first_response_due_at",
            "resolution_due_at",
            "first_response_at",
            "resolved_at",
            "closed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_sla_state(self, ticket):
        # Not setdefault("now", timezone.now()): its argument would read the clock
        # for every ticket, even though only the first value is kept.
        if "now" not in self.context:
            self.context["now"] = timezone.now()
        return sla_state(ticket, self.context["now"])


class TicketSerializer(TicketListSerializer):
    """One ticket in full, with what the requesting user may do with its status.

    Needs the request in the context (allowed_transitions depends on the user).
    """

    allowed_transitions = serializers.SerializerMethodField()

    class Meta(TicketListSerializer.Meta):
        fields = [*TicketListSerializer.Meta.fields, "description", "allowed_transitions"]
        read_only_fields = fields

    def get_allowed_transitions(self, ticket):
        return allowed_transitions(ticket, self.context["request"].user)


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


class TransitionSerializer(serializers.Serializer):
    """The target status of a transition. Whether it is allowed is decided by
    the state machine, not here: the transition table has a single source.
    """

    to_status = serializers.ChoiceField(choices=Status.choices)


class TicketUpdateSerializer(serializers.Serializer):
    """What staff may change on a ticket. Every field is optional (PATCH).

    Fields that cannot be edited here are rejected with a 400 instead of being
    silently ignored, which is what DRF does by default: the client would believe
    it had changed the status, for example, when nothing happened.
    """

    # Active staff only; the service checks it again for other callers.
    assignee = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role__in=[Role.AGENT, Role.ADMIN], is_active=True),
        allow_null=True,
        required=False,
    )
    priority = serializers.ChoiceField(choices=Priority.choices, required=False)
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(is_active=True), required=False
    )

    READ_ONLY_HINTS = {
        "status": "Usa /transitions/ para cambiar el estado.",
    }

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {
                    name: self.READ_ONLY_HINTS.get(name, "Este campo no se puede modificar.")
                    for name in sorted(unknown)
                }
            )
        return attrs


class CommentAuthorSerializer(serializers.ModelSerializer):
    """The role lets the interface tell the support team from the client."""

    class Meta:
        model = User
        fields = ["id", "full_name", "role"]
        read_only_fields = fields


class CommentSerializer(serializers.ModelSerializer):
    author = CommentAuthorSerializer(read_only=True)

    class Meta:
        model = TicketComment
        fields = ["id", "author", "body", "is_internal", "created_at"]
        read_only_fields = fields


class CommentCreateSerializer(serializers.Serializer):
    # CharField trims and rejects blank values: "   " is a 400 with a field error.
    body = serializers.CharField()
    is_internal = serializers.BooleanField(default=False)


class EventDisplay:
    """Readable values for the events of one page, resolved in bulk.

    Events store ids and codes ("7" -> "12", "pending"). A client cannot turn an
    agent id into a name, so the API does it: one query for the users and one for
    the categories of the whole page, instead of one per event.
    """

    def __init__(self, events):
        user_ids, category_ids = set(), set()
        for event in events:
            # Only these two event types store ids; the others store codes.
            target = {
                EventType.ASSIGNEE_CHANGED: user_ids,
                EventType.CATEGORY_CHANGED: category_ids,
            }.get(event.event_type)
            if target is not None:
                target |= {int(v) for v in (event.from_value, event.to_value) if v is not None}
        self.users = (
            dict(User.objects.filter(pk__in=user_ids).values_list("id", "full_name"))
            if user_ids
            else {}
        )
        self.categories = (
            dict(Category.objects.filter(pk__in=category_ids).values_list("id", "name"))
            if category_ids
            else {}
        )

    def __call__(self, event, value):
        if value is None:
            return None
        if event.event_type == EventType.STATUS_CHANGED:
            return Status(value).label
        if event.event_type == EventType.PRIORITY_CHANGED:
            return Priority(value).label
        if event.event_type == EventType.ASSIGNEE_CHANGED:
            return self.users.get(int(value))
        if event.event_type == EventType.CATEGORY_CHANGED:
            return self.categories.get(int(value))
        return value


class EventSerializer(serializers.ModelSerializer):
    """One history entry. Needs an EventDisplay as context["display"]."""

    actor = UserSummarySerializer(read_only=True, allow_null=True)
    from_display = serializers.SerializerMethodField()
    to_display = serializers.SerializerMethodField()

    class Meta:
        model = TicketEvent
        fields = [
            "id",
            "event_type",
            "actor",
            "from_value",
            "to_value",
            "from_display",
            "to_display",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields

    def get_from_display(self, event):
        return self.context["display"](event, event.from_value)

    def get_to_display(self, event):
        return self.context["display"](event, event.to_value)
