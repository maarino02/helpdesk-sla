"""The page templates, their static files and what their scripts assume.

The scripts themselves are checked by hand in the browser (phase 9 plan, D7).
These tests cover what can go wrong on the Django side: a page that does not
render, code the Content-Security-Policy would block, a static file that
collectstatic would not find or whose imports it would leave unhashed, and URLs
written in the JavaScript that no longer match Django's.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.urls import reverse

from apps.core.tests.html import inline_code

STATIC = Path(__file__).resolve().parent.parent / "static" / "web"
SCRIPTS = sorted(STATIC.rglob("*.js"))
PAGES = ["web:home", "web:login"]


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def static_path(url):
    """The file behind a static URL: /static/web/x.js -> web/x.js."""
    assert url.startswith(settings.STATIC_URL), url
    return url.removeprefix(settings.STATIC_URL)


def tags(html, name):
    parser = Tags()
    parser.feed(html)
    return [attrs for tag, attrs in parser.tags if tag == name]


@pytest.mark.parametrize("page", PAGES)
def test_pages_render_without_inline_code(client, page):
    response = client.get(reverse(page))

    assert response.status_code == 200
    assert response["Content-Security-Policy"] == settings.CONTENT_SECURITY_POLICY
    assert inline_code(response.content.decode()) == ([], [])


@pytest.mark.parametrize("page", PAGES)
def test_each_page_loads_one_module_that_exists(client, page):
    html = client.get(reverse(page)).content.decode()

    [script] = tags(html, "script")
    assert script["type"] == "module"
    assert finders.find(static_path(script["src"])), script["src"]
    [stylesheet] = [link for link in tags(html, "link") if link.get("rel") == "stylesheet"]
    assert finders.find(static_path(stylesheet["href"])), stylesheet["href"]


def test_pages_with_a_session_start_hidden(client):
    def main(page):
        [attributes] = tags(client.get(reverse(page)).content.decode(), "main")
        return attributes

    # Shown by layout.js once /api/me/ answers; the login page needs no session.
    assert "hidden" in main("web:home")
    assert "hidden" not in main("web:login")


def test_the_login_form_never_puts_the_password_in_the_url(client):
    html = client.get(reverse("web:login")).content.decode()

    [form] = tags(html, "form")
    assert form["method"] == "post"  # If the script fails, a GET would.
    autocomplete = {i["name"]: i["autocomplete"] for i in tags(html, "input")}
    assert autocomplete == {"email": "username", "password": "current-password"}


def test_the_urls_in_the_scripts_match_django():
    api = (STATIC / "js" / "api.js").read_text(encoding="utf-8")
    layout = (STATIC / "js" / "layout.js").read_text(encoding="utf-8")

    assert f'LOGIN_PAGE = "{reverse("web:login")}"' in api
    assert f'SESSION = "{reverse("accounts:session")}"' in api
    assert reverse("accounts:session-refresh") == reverse("accounts:session") + "refresh/"
    assert reverse("accounts:session-logout") == reverse("accounts:session") + "logout/"
    assert f'"{reverse("accounts:me")}"' in layout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_scripts_never_write_html(script):
    # User data goes into the page as text (textContent), never parsed as HTML:
    # a ticket titled "<img src=x onerror=...>" must show up as those characters.
    code = script.read_text(encoding="utf-8")

    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", code)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_imports_have_the_form_collectstatic_rewrites(script):
    # Relative, in double or single quotes, ending with ";" (apps/core/storage.py).
    code = script.read_text(encoding="utf-8")
    imports = re.findall(r"^import .*$", code, flags=re.MULTILINE)

    for statement in imports:
        assert re.fullmatch(r"""import .* from ["']\.{1,2}/[\w/]+\.js["'];""", statement)


def test_collectstatic_hashes_the_modules_and_their_imports(client, settings, tmp_path):
    settings.STATIC_ROOT = tmp_path
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "apps.core.storage.StaticFilesStorage"},
    }
    # Every static file of the project, the admin's and DRF's included: the import
    # rewriting applies to all of them and must not trip on any.
    call_command("collectstatic", interactive=False, verbosity=0)

    # The page links the hashed module...
    html = client.get(reverse("web:home")).content.decode()
    [script] = tags(html, "script")
    hashed = re.fullmatch(r"/static/(web/js/pages/home\.[0-9a-f]{12}\.js)", script["src"])
    assert hashed, script["src"]
    # ...which imports the hashed layout, which imports the hashed api.
    home = (tmp_path / hashed.group(1)).read_text(encoding="utf-8")
    layout_name = re.search(r'from "\.\./(layout\.[0-9a-f]{12}\.js)";', home)
    assert layout_name, home
    layout = (tmp_path / "web" / "js" / layout_name.group(1)).read_text(encoding="utf-8")
    assert re.search(r'from "\./api\.[0-9a-f]{12}\.js";', layout), layout
