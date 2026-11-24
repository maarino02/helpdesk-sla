"""One transaction around each admin request, and the Content-Security-Policy header."""

from django.conf import settings
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


class ContentSecurityPolicyMiddleware:
    """Send settings.CONTENT_SECURITY_POLICY with every response (ADR-0006).

    The browser then runs only scripts and styles served by the site itself: no
    inline <script>, no style="..." attributes, nothing from other origins. An
    injected script tag would not run, which is what keeps the access token the
    page holds in memory out of reach of an XSS flaw.

    Every response gets it, JSON and static files included: harmless there, and a
    page served from any URL is covered without having to remember it. A view
    that needs another policy can set its own header; it is not overwritten.
    Goes right after SecurityMiddleware, before WhiteNoise, which answers static
    requests without calling the middleware below it.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.policy = settings.CONTENT_SECURITY_POLICY

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", self.policy)
        return response
