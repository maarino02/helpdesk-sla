"""JWT authentication that builds the row-level security context (ADR-0004)."""

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.settings import api_settings

from apps.core import rls


class RlsJWTAuthentication(JWTAuthentication):
    """simplejwt checks signature and expiry; the user comes from activate_user.

    The stock get_user queries app_user before any context exists, so it would
    find nobody. activate_user sets app.user_id, reads the user's own row and
    widens the context to their current role and organization.

    The default authentication class for every DRF view. DRF authenticates in
    APIView.initial(), inside the request transaction (ATOMIC_REQUESTS), so the
    context lasts for the whole view.
    """

    def get_user(self, validated_token):
        try:
            user_id = validated_token[api_settings.USER_ID_CLAIM]
        except KeyError as error:
            raise InvalidToken(_("Token contained no recognizable user identification")) from error

        user = rls.activate_user(user_id)
        if user is None:
            # Unknown or deactivated since the token was issued: one message for
            # both, so the response does not reveal which.
            raise AuthenticationFailed(_("User not found or inactive"), code="user_inactive")
        return user
