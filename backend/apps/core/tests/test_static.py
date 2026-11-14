from django.core.management import call_command


def test_whitenoise_serves_collected_static_files(client, settings, tmp_path):
    # Collect into a temporary STATIC_ROOT. WhiteNoise scans it when the
    # middleware is created, which happens on this test client's first request.
    settings.STATIC_ROOT = tmp_path
    call_command("collectstatic", interactive=False, verbosity=0)

    response = client.get("/static/admin/css/base.css")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/css")
