from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import IsStaff
from apps.organizations.models import Organization
from apps.organizations.serializers import OrganizationOptionSerializer


class OrganizationListView(generics.ListAPIView):
    """Every client, active ones first, by name: the client filter of the dashboard.

    Deactivated clients are listed too: they keep their tickets, and their
    metrics for the months they were active are still worth looking at. Staff
    only (a client's metrics are always their own). Not paginated: the select
    needs every name.
    """

    permission_classes = [IsAuthenticated, IsStaff]
    serializer_class = OrganizationOptionSerializer
    pagination_class = None
    queryset = Organization.objects.order_by("-is_active", "name", "id")
