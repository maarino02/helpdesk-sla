from rest_framework import serializers

from apps.organizations.models import Organization


class OrganizationOptionSerializer(serializers.ModelSerializer):
    """A client to choose in a filter. is_active tells former clients apart."""

    class Meta:
        model = Organization
        fields = ["id", "name", "is_active"]
        read_only_fields = fields
