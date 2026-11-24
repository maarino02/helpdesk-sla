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

from apps.tickets.models import Ticket

app_name = "web"


def page(template_name, **context):
    # No ATOMIC_REQUESTS transaction: it would open a database connection for a
    # page that never queries. The tests run without database access to keep it so.
    view = TemplateView.as_view(template_name=template_name, extra_context=context)
    return transaction.non_atomic_requests(view)


urlpatterns = [
    path("", page("web/tickets.html"), name="tickets"),
    path(
        "tickets/new/",
        # The form's limit is the column's, so the browser stops at the same length.
        page("web/ticket_new.html", title_max_length=Ticket._meta.get_field("title").max_length),
        name="ticket-new",
    ),
    # The id reaches the template (TemplateView passes the URL's arguments), and
    # from there the script: the page itself does not look the ticket up.
    path("tickets/<int:pk>/", page("web/ticket.html"), name="ticket"),
    path("login/", page("web/login.html"), name="login"),
]
