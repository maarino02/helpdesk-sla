"""The pages of the web frontend.

Each page is a template that only lays out the screen and loads its JavaScript
module; the data comes from the API, with the user's token (ADR-0006). So these
views read nothing from the database and need no authentication of their own: a
page opened without a session sends the browser to the login page from its
script, and without a token the API returns nothing anyway.
"""

from django.db import transaction
from django.urls import path
from django.views.generic import TemplateView

app_name = "web"


def page(template_name):
    # No ATOMIC_REQUESTS transaction: it would open a database connection for a
    # page that never queries. The tests run without database access to keep it so.
    return transaction.non_atomic_requests(TemplateView.as_view(template_name=template_name))


urlpatterns = [
    path("", page("web/home.html"), name="home"),
    path("login/", page("web/login.html"), name="login"),
]
