from unittest import mock

import pytest
from django.db import OperationalError
from django.urls import reverse

LIVE_URL = reverse("core:health-live")
READY_URL = reverse("core:health-ready")


def test_live_returns_ok_without_touching_the_database(client):
    # No django_db mark: pytest-django blocks database access, so this test
    # also proves liveness does not depend on PostgreSQL.
    response = client.get(LIVE_URL)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_ready_returns_ok_when_database_is_reachable(client):
    response = client.get(READY_URL)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


@pytest.mark.django_db
def test_ready_returns_503_without_leaking_error_details(client):
    error = OperationalError("connection to server at 10.0.0.5 failed")
    with mock.patch("apps.core.views.connection.cursor", side_effect=error):
        response = client.get(READY_URL)

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "error"}}
    assert "10.0.0.5" not in response.content.decode()


@pytest.mark.parametrize("url", [LIVE_URL, READY_URL])
def test_health_endpoints_only_accept_get(client, url):
    response = client.post(url)

    assert response.status_code == 405


@pytest.mark.django_db
def test_health_is_exempt_from_https_redirect(client, settings):
    settings.SECURE_SSL_REDIRECT = True

    # Other paths are still redirected to HTTPS; health checks are not.
    assert client.get("/admin/").status_code == 301
    assert client.get(LIVE_URL).status_code == 200
    assert client.get(READY_URL).status_code == 200
