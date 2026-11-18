"""Role-based permissions: the first layer, before row-level security.

The role is read from the database on every request (ADR-0004), so a change
applies to the next request. Combine them with the default IsAuthenticated,
for example permission_classes = [IsAuthenticated, IsStaff].
"""

from rest_framework.permissions import BasePermission

from apps.accounts.models import Role


class _HasRole(BasePermission):
    roles = frozenset()

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in self.roles)


class IsClient(_HasRole):
    roles = frozenset({Role.CLIENT})


class IsStaff(_HasRole):
    """Agents and admins."""

    roles = frozenset({Role.AGENT, Role.ADMIN})


class IsAdmin(_HasRole):
    roles = frozenset({Role.ADMIN})
