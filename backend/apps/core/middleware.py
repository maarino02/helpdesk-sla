"""One transaction around each admin request, rendering included."""

from django.db import transaction
from django.urls import NoReverseMatch, reverse


class AdminRlsMiddleware:
    """Keep the admin's view and template rendering in one transaction.

    The admin returns TemplateResponse objects, which Django renders after the
    view returns, outside the ATOMIC_REQUESTS transaction. Querysets the templates
    evaluate (select choices, related objects) would then run in autocommit with
    no row-level security context and silently see nothing: an empty organization
    <select>, for instance.

    Inside this transaction, the first access to request.user loads the session
    user through RlsModelBackend.get_user, which sets the context for the whole
    request; the view's ATOMIC_REQUESTS block becomes a savepoint within it.
    Login goes through here too, so last_login is written in the same context.

    Only the admin uses sessions. DRF responses are also rendered after the view,
    but they only encode data the serializer already read inside the transaction.
    Must come after AuthenticationMiddleware, which sets the lazy request.user.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        try:
            self.admin_prefix = reverse("admin:index")
        except NoReverseMatch:
            # A URLconf without the admin (some tests use their own).
            self.admin_prefix = None

    def __call__(self, request):
        if self.admin_prefix is None or not request.path.startswith(self.admin_prefix):
            return self.get_response(request)
        with transaction.atomic():
            return self.get_response(request)
