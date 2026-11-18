from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts.serializers import LoginSerializer, MeSerializer, RefreshSerializer


class LoginView(TokenObtainPairView):
    """POST email and password, get an access and a refresh token.

    No authentication (simplejwt's views clear it): an expired token in the
    header must not block logging in again. Throttled per client address against
    password guessing (rate in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]).
    """

    serializer_class = LoginSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class RefreshView(TokenRefreshView):
    """POST a refresh token, get a new access token."""

    serializer_class = RefreshSerializer


class MeView(APIView):
    """The authenticated user, with role and organization as they are now."""

    def get(self, request):
        return Response(MeSerializer(request.user).data)
