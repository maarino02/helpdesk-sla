"""Mixins shared by the ModelAdmin classes of several apps."""

from apps.accounts.models import Role


class AdminRoleRequiredMixin:
    """Only users with the admin role may add, change or delete; others may view.

    Row-level security only lets app.role = 'admin' write catalogs and create
    organizations. Django's own permissions are checked too, but they are not
    enough: an agent with is_staff (even a superuser) would get the form and then
    a database error on save, that is, a 500. The ANDs keep any restriction a
    subclass or Django adds.

    Works on ModelAdmin and InlineModelAdmin: the inline versions of these methods
    take an extra obj argument, hence *args.
    """

    def _has_admin_role(self, request):
        return request.user.role == Role.ADMIN

    def has_add_permission(self, request, *args):
        return self._has_admin_role(request) and super().has_add_permission(request, *args)

    def has_change_permission(self, request, obj=None):
        return self._has_admin_role(request) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._has_admin_role(request) and super().has_delete_permission(request, obj)
