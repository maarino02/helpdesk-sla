"""The Content-Security-Policy header (ADR-0006) and the pages that must work with it."""

import pytest
from django.conf import settings
from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory

from apps.core.middleware import ContentSecurityPolicyMiddleware
from apps.core.tests.html import inline_code

HEADER = "Content-Security-Policy"


def directives():
    return dict(part.strip().split(" ", 1) for part in settings.CONTENT_SECURITY_POLICY.split(";"))


def test_the_policy_allows_only_the_sites_own_resources():
    policy = directives()

    assert policy["default-src"] == "'self'"
    assert policy["script-src"] == "'self'"
    assert policy["style-src"] == "'self'"
    assert (policy["object-src"], policy["base-uri"]) == ("'none'", "'none'")
    assert policy["frame-ancestors"] == "'none'"
    assert "unsafe" not in settings.CONTENT_SECURITY_POLICY


def test_it_goes_before_whitenoise():
    # WhiteNoise answers static requests itself: a middleware after it would never
    # see those responses.
    middleware = settings.MIDDLEWARE
    assert middleware.index("apps.core.middleware.ContentSecurityPolicyMiddleware") < (
        middleware.index("whitenoise.middleware.WhiteNoiseMiddleware")
    )


def test_api_responses_carry_it(client):
    response = client.get("/health/live/")

    assert response[HEADER] == settings.CONTENT_SECURITY_POLICY


def test_static_files_carry_it(client, settings, tmp_path):
    settings.STATIC_ROOT = tmp_path
    call_command("collectstatic", interactive=False, verbosity=0)

    response = client.get("/static/admin/css/base.css")

    assert response.status_code == 200
    assert response[HEADER] == settings.CONTENT_SECURITY_POLICY


def test_a_views_own_policy_is_kept():
    def view(request):
        response = HttpResponse()
        response[HEADER] = "default-src 'none'"
        return response

    response = ContentSecurityPolicyMiddleware(view)(RequestFactory().get("/"))

    assert response[HEADER] == "default-src 'none'"


def test_the_checker_finds_inline_code():
    # Guard for the admin tests: a checker that never finds anything passes them all.
    html = """
        <script>alert(1)</script>
        <script type="module">import "./x.js";</script>
        <script src="/static/app.js"></script>
        <script type="application/json" id="data">{}</script>
        <p style="color: red">Hola</p>
    """

    assert inline_code(html) == (["?", "?"], ["p"])


@pytest.mark.django_db
def test_the_admin_login_page_works_under_the_policy(client):
    response = client.get("/admin/login/")

    assert response.status_code == 200
    assert inline_code(response.content.decode()) == ([], [])


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/admin/", "/admin/tickets/category/add/", "/admin/sla/"])
def test_admin_pages_work_under_the_policy(admin_client, url):
    response = admin_client.get(url)

    assert response.status_code == 200
    assert response[HEADER] == settings.CONTENT_SECURITY_POLICY
    assert inline_code(response.content.decode()) == ([], [])
