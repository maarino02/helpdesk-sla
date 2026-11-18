"""REST API defaults and token settings (ADR-0004)."""

from datetime import timedelta

import jwt
import pytest
from django.conf import settings
from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.tests.factories import AgentUserFactory


class UndeclaredPermissionsView(APIView):
    """An endpoint whose author forgot to declare authentication or permissions."""

    def get(self, request):
        return Response({"secret": "data"})


# This module doubles as the URLconf of the tests marked with it.
urlpatterns = [path("undeclared/", UndeclaredPermissionsView.as_view())]


@pytest.mark.urls(__name__)
@pytest.mark.django_db  # ATOMIC_REQUESTS opens a transaction for every request.
def test_endpoints_deny_by_default(client):
    response = client.get("/undeclared/")

    # 403, not 401: no authentication class is configured yet, so there is no
    # WWW-Authenticate challenge to send.
    assert response.status_code == 403
    assert "secret" not in response.content.decode()
    assert response["Content-Type"] == "application/json"


def test_access_token_carries_only_the_user_id():
    user = AgentUserFactory.build(id=42)

    claims = jwt.decode(
        str(AccessToken.for_user(user)),
        settings.SIMPLE_JWT["SIGNING_KEY"],
        algorithms=["HS256"],
    )

    # No role or organization: they are read from the database on every request.
    assert set(claims) == {"token_type", "exp", "iat", "jti", "user_id"}
    assert claims["user_id"] == "42"
    assert claims["exp"] - claims["iat"] == timedelta(minutes=15).total_seconds()


def test_refresh_token_lives_one_day():
    token = RefreshToken.for_user(AgentUserFactory.build(id=42))

    assert token["exp"] - token["iat"] == timedelta(days=1).total_seconds()


# Verifying with the short test SECRET_KEY is the point here, so PyJWT's warning
# about short HMAC keys is expected.
@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
def test_tokens_are_not_signed_with_the_secret_key():
    token = str(AccessToken.for_user(AgentUserFactory.build(id=42)))

    assert settings.SIMPLE_JWT["SIGNING_KEY"] != settings.SECRET_KEY
    assert len(settings.SIMPLE_JWT["SIGNING_KEY"]) >= 32  # Minimum for HS256.
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
