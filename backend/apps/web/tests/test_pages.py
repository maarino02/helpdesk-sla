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
from apps.sla.models import Priority
from apps.tickets.models import EventType, Status, Ticket
from apps.tickets.services.deadlines import SlaState
from apps.tickets.services.state_machine import TRANSITIONS

STATIC = Path(__file__).resolve().parent.parent / "static" / "web"
SCRIPTS = sorted(STATIC.rglob("*.js"))
# (name, URL arguments): the detail page needs a ticket id, any id.
PAGES = [("web:tickets", []), ("web:ticket-new", []), ("web:ticket", [7]), ("web:login", [])]


def url(page):
    name, args = page if isinstance(page, tuple) else (page, [])
    return reverse(name, args=args)


JS = STATIC / "js"


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


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page[0])
def test_pages_render_without_inline_code(client, page):
    response = client.get(url(page))

    assert response.status_code == 200
    assert response["Content-Security-Policy"] == settings.CONTENT_SECURITY_POLICY
    assert inline_code(response.content.decode()) == ([], [])


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page[0])
def test_each_page_loads_one_module_that_exists(client, page):
    html = client.get(url(page)).content.decode()

    [script] = tags(html, "script")
    assert script["type"] == "module"
    assert finders.find(static_path(script["src"])), script["src"]
    [stylesheet] = [link for link in tags(html, "link") if link.get("rel") == "stylesheet"]
    assert finders.find(static_path(stylesheet["href"])), stylesheet["href"]


def test_pages_with_a_session_start_hidden(client):
    def main(page):
        [attributes] = tags(client.get(url(page)).content.decode(), "main")
        return attributes

    # Shown by layout.js once /api/me/ answers; the login page needs no session.
    assert "hidden" in main("web:tickets")
    assert "hidden" in main("web:ticket-new")
    assert "hidden" in main(("web:ticket", [7]))
    assert "hidden" not in main("web:login")


@pytest.mark.parametrize("page", ["web:login", "web:ticket-new"])
def test_forms_post_if_the_script_fails(client, page):
    # Without the script, a GET form would put what was typed (a password) in the URL.
    [form] = tags(client.get(url(page)).content.decode(), "form")

    assert form["method"] == "post"


def test_the_login_form_lets_password_managers_fill_it(client):
    html = client.get(reverse("web:login")).content.decode()

    autocomplete = {i["name"]: i["autocomplete"] for i in tags(html, "input")}
    assert autocomplete == {"email": "username", "password": "current-password"}


def test_the_detail_page_hands_the_id_to_its_script(client):
    html = client.get(reverse("web:ticket", args=[42])).content.decode()

    [container] = [d for d in tags(html, "div") if d.get("id") == "ticket"]
    assert container["data-ticket-id"] == "42"
    # Every form of the page posts, as the others (see above).
    assert {form["method"] for form in tags(html, "form")} == {"post"}


def test_every_transition_has_a_button_label():
    code = (JS / "pages" / "ticket.js").read_text(encoding="utf-8")
    block = re.search(r"const TRANSITION_LABELS = {(.*?)};", code, flags=re.DOTALL)
    labelled = set(re.findall(r'"(\w+)>(\w+)": "[^"]+"', block.group(1)))

    assert labelled == set(TRANSITIONS)


def test_every_event_type_is_described():
    code = (JS / "pages" / "ticket.js").read_text(encoding="utf-8")

    missing = [value for value in EventType.values if f'case "{value}":' not in code]
    assert missing == []


def test_the_title_field_stops_at_the_columns_length(client):
    html = client.get(reverse("web:ticket-new")).content.decode()

    [title] = [i for i in tags(html, "input") if i["name"] == "title"]
    assert int(title["maxlength"]) == Ticket._meta.get_field("title").max_length


@pytest.mark.parametrize(
    ("name", "choices"),
    [("STATUS", Status.choices), ("PRIORITY", Priority.choices), ("SLA_STATE", SlaState.choices)],
)
def test_the_labels_match_the_choices(name, choices):
    # The same codes in the same order, with the same Spanish labels.
    code = (JS / "labels.js").read_text(encoding="utf-8")
    block = re.search(rf"export const {name} = {{(.*?)}};", code, flags=re.DOTALL)

    assert block, name
    assert re.findall(r'(\w+): "([^"]*)"', block.group(1)) == list(choices)


def test_the_urls_in_the_scripts_match_django():
    api = (JS / "api.js").read_text(encoding="utf-8")
    layout = (JS / "layout.js").read_text(encoding="utf-8")
    tickets = (JS / "pages" / "tickets.js").read_text(encoding="utf-8")
    ticket_new = (JS / "pages" / "ticket_new.js").read_text(encoding="utf-8")

    assert f'LOGIN_PAGE = "{reverse("web:login")}"' in api
    assert f'SESSION = "{reverse("accounts:session")}"' in api
    assert reverse("accounts:session-refresh") == reverse("accounts:session") + "refresh/"
    assert reverse("accounts:session-logout") == reverse("accounts:session") + "logout/"
    assert f'"{reverse("accounts:me")}"' in layout
    assert f'"{reverse("accounts:staff")}"' in tickets
    assert f"`{reverse('tickets:list')}?" in tickets
    assert f'"{reverse("tickets:categories")}"' in ticket_new
    assert f'"{reverse("tickets:list")}"' in ticket_new
    # The detail: the page URL in the list and after creating, the API URLs in ticket.js.
    detail_page = reverse("web:ticket", args=[0]).replace("0", "${ticket.id}")
    assert f"`{detail_page}`" in tickets
    assert f"`{detail_page}`" in ticket_new
    ticket = (JS / "pages" / "ticket.js").read_text(encoding="utf-8")
    detail_api = reverse("tickets:detail", args=[0])
    assert f"`{detail_api.replace('0', '${id}')}`" in ticket
    suffixes = {"transitions/", "comments/", "events/"}
    for suffix in suffixes:
        assert reverse(f"tickets:{suffix.rstrip('/')}", args=[0]) == detail_api + suffix
    # Every use, not just one: comments/ is read and written in different places.
    used = re.findall(r"`\$\{TICKET_URL\}([^`]*)`", ticket)
    assert used and set(used) == suffixes, used


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
    html = client.get(reverse("web:tickets")).content.decode()
    [script] = tags(html, "script")
    hashed = re.fullmatch(r"/static/(web/js/pages/tickets\.[0-9a-f]{12}\.js)", script["src"])
    assert hashed, script["src"]
    # ...which imports the hashed layout, which imports the hashed api.
    page = (tmp_path / hashed.group(1)).read_text(encoding="utf-8")
    layout_name = re.search(r'from "\.\./(layout\.[0-9a-f]{12}\.js)";', page)
    assert layout_name, page
    layout = (tmp_path / "web" / "js" / layout_name.group(1)).read_text(encoding="utf-8")
    assert re.search(r'from "\./api\.[0-9a-f]{12}\.js";', layout), layout
