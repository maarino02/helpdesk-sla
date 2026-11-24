from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, UnsupportedMediaType
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
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


class SessionView(APIView):
    """Base of the browser session views (ADR-0006).

    No authentication: the credential is the password or the refresh cookie, and
    an expired access token must not block either. JSON only, checked before
    anything else, against cross-site requests: a form from another site can only
    send form or plain-text bodies. DRF's parsers would not be enough, because
    they only look at the content type when the body is read, and not at all when
    it is empty.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def initial(self, request, *args, **kwargs):
        if request.content_type.split(";")[0].strip() != "application/json":
            raise UnsupportedMediaType(request.content_type)
        super().initial(request, *args, **kwargs)

    def get_authenticate_header(self, request):
        # Without authentication classes DRF turns a failed login or refresh into a
        # 403; a header to send with the 401 keeps it a 401, as in simplejwt's views.
        return 'Bearer realm="api"'


class SessionLoginView(SessionView):
    """POST email and password: the access token in the body, the refresh in the cookie."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        response = Response({"access": serializer.validated_data["access"]})
        set_refresh_cookie(response, serializer.validated_data["refresh"])
        return response


class SessionRefreshView(SessionView):
    """POST with the cookie: a new access token, while the user is still active."""

    def post(self, request):
        token = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
        if not token:
            # detail and code in the body, the shape of simplejwt's own 401s.
            raise AuthenticationFailed(
                {"detail": "No hay ninguna sesión iniciada.", "code": "no_session"}
            )
        serializer = RefreshSerializer(data={"refresh": token})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as error:  # Expired or tampered with.
            raise InvalidToken(error.args[0]) from error
        return Response(serializer.validated_data)


class SessionLogoutView(SessionView):
    """POST: removes the cookie. The page forgets its access token by itself."""

    def post(self, request):
        response = Response(status=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(
            settings.REFRESH_COOKIE_NAME, path=settings.REFRESH_COOKIE_PATH, samesite="Strict"
        )
        return response


def set_refresh_cookie(response, token: str) -> None:
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        token,
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        path=settings.REFRESH_COOKIE_PATH,
        secure=settings.REFRESH_COOKIE_SECURE,
        httponly=True,  # Not readable from JavaScript.
        samesite="Strict",  # Never sent with a request started from another site.
    )
