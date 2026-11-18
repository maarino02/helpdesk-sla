"""Every DRF view in the URLconf runs with the row-level security context.

RlsJWTAuthentication, the default authentication class, is what sets the
context. A view that replaces authentication_classes would run without it (and
see nothing), so it must be on the allow-list below with its reason.
"""

from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.views import APIView

from apps.accounts.authentication import RlsJWTAuthentication

# Views that authenticate their own way, by URL name.
WITHOUT_JWT = {
    "accounts:token": "login: credentials, not a token",
    "accounts:token-refresh": "the refresh token is the credential",
}


def drf_views(patterns=None, namespace=""):
    for pattern in get_resolver().url_patterns if patterns is None else patterns:
        if isinstance(pattern, URLResolver):
            inner = f"{namespace}{pattern.namespace}:" if pattern.namespace else namespace
            yield from drf_views(pattern.url_patterns, inner)
        elif isinstance(pattern, URLPattern):
            view_class = getattr(pattern.callback, "cls", None)
            if view_class is not None and issubclass(view_class, APIView):
                yield f"{namespace}{pattern.name}", view_class


def test_the_urlconf_has_drf_views():
    # Guard for the test below: an empty list would make it pass vacuously.
    assert {"accounts:me", "accounts:token"} <= {name for name, _ in drf_views()}


def test_every_drf_view_uses_the_row_level_security_authentication():
    offenders = {
        name: [cls.__name__ for cls in view_class.authentication_classes]
        for name, view_class in drf_views()
        if name not in WITHOUT_JWT and RlsJWTAuthentication not in view_class.authentication_classes
    }

    assert offenders == {}


def test_allow_list_has_no_stale_entries():
    names = {name for name, _ in drf_views()}

    assert set(WITHOUT_JWT) <= names
