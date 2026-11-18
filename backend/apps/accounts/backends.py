"""Authentication backend that builds the row-level security context (ADR-0004)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.hashers import check_password, get_hasher, identify_hasher
from django.db import transaction

from apps.core import rls


class RlsModelBackend(ModelBackend):
    """ModelBackend whose queries run with the right context.

    Permission methods (has_perm and friends) are inherited unchanged: they read
    Django's group and permission tables, which have no row-level security.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        if username is None:
            username = kwargs.get(user_model.USERNAME_FIELD)
        if username is None or password is None:
            return None

        # A savepoint inside a request; its own transaction from the shell.
        with transaction.atomic():
            rls.login_context(username)
            try:
                user = user_model._default_manager.get_by_natural_key(username)
            except user_model.DoesNotExist:
                # Hash anyway, so an unknown email takes as long as a wrong
                # password and response times do not reveal which emails exist.
                user_model().set_password(password)
                rls.clear_context()
                return None

            # No setter, unlike user.check_password(): it would save an upgraded
            # hash right away, and nothing can be written in the login context.
            if not (check_password(password, user.password) and self.user_can_authenticate(user)):
                rls.clear_context()
                return None

            # From here on the user's own context: django.contrib.auth.login() and
            # the login view can update last_login.
            user = rls.activate_user(user.pk)
            if user is not None and _needs_rehash(user.password):
                user.set_password(password)
                user.save(update_fields=["password"])
            return user

    def get_user(self, user_id):
        """Load the user of a session on every admin request."""
        with transaction.atomic():
            return rls.activate_user(user_id)


def _needs_rehash(encoded):
    """Same rule as Django's check_password: other algorithm or outdated settings."""
    try:
        hasher = identify_hasher(encoded)
    except ValueError:
        return False
    preferred = get_hasher("default")
    return hasher.algorithm != preferred.algorithm or preferred.must_update(encoded)
