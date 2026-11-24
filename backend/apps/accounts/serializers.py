from django.contrib.auth.models import update_last_login
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.settings import api_settings

from apps.accounts.models import User
from apps.core import rls
from apps.organizations.models import Organization


class LoginSerializer(TokenObtainPairSerializer):
    """simplejwt's login, plus last_login.

    authenticate() goes through RlsModelBackend, which leaves the user's own
    context active on success, so last_login can be written here.
    UPDATE_LAST_LOGIN stays off in simplejwt for clarity: this is where it happens.
    """

    def validate(self, attrs):
        data = super().validate(attrs)
        update_last_login(None, self.user)
        return data


class RefreshSerializer(TokenRefreshSerializer):
    """New access token, only while the user still exists and is active.

    Replaces simplejwt's validate, which looks the user up without context: under
    row-level security that query finds nobody and raises DoesNotExist (a 500).
    No rotation or blacklist (ADR-0004), so only an access token is returned.
    """

    def validate(self, attrs):
        refresh = self.token_class(attrs["refresh"])
        if rls.activate_user(refresh.payload.get(api_settings.USER_ID_CLAIM)) is None:
            raise AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            )
        return {"access": str(refresh.access_token)}


class OrganizationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name"]


class MeSerializer(serializers.ModelSerializer):
    """The authenticated user. Explicit fields: the password hash never leaves."""

    organization = OrganizationSummarySerializer(read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "role", "organization"]
        read_only_fields = fields


class StaffMemberSerializer(serializers.ModelSerializer):
    """Someone a ticket can be assigned to. No email: the name is enough to choose."""

    class Meta:
        model = User
        fields = ["id", "full_name", "role"]
        read_only_fields = fields
