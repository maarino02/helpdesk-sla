"""Every DRF view runs with the row-level security context and is documented.

RlsJWTAuthentication, the default authentication class, is what sets the
context. A view that replaces authentication_classes would run without it (and
see nothing), so it must be on the allow-list below with its reason.
"""

import re
from pathlib import Path

from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.views import APIView

from apps.accounts.authentication import RlsJWTAuthentication

# Views that authenticate their own way, by URL name.
WITHOUT_JWT = {
    "accounts:token": "login: credentials, not a token",
    "accounts:token-refresh": "the refresh token is the credential",
    "accounts:session": "browser login: credentials, not a token (ADR-0006)",
    "accounts:session-refresh": "the refresh cookie is the credential",
    "accounts:session-logout": "only removes the cookie; must work with an expired token",
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
    assert {"accounts:me", "accounts:token", "tickets:list"} <= {name for name, _ in drf_views()}


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


API_REFERENCE = Path(settings.BASE_DIR).parent / "docs" / "api.md"


def api_routes(patterns=None, prefix=""):
    """Every route under /api/, written as in the docs: <int:pk> becomes {id}."""
    for pattern in get_resolver().url_patterns if patterns is None else patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from api_routes(pattern.url_patterns, route)
        elif route.startswith("api/"):
            yield "/" + re.sub(r"<int:pk>", "{id}", route)


def test_every_api_route_is_in_the_api_reference():
    reference = API_REFERENCE.read_text(encoding="utf-8")
    routes = set(api_routes())

    # Guard: an empty set would make the check pass vacuously.
    assert "/api/tickets/{id}/transitions/" in routes
    # Inside backticks, as the tables write them: `/api/tickets/` must not count
    # as documented just because `/api/tickets/{id}/` is.
    missing = sorted(r for r in routes if f" {r}`" not in reference)
    assert missing == []
