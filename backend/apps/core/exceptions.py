"""Domain exceptions and their translation into HTTP responses, in one place.

Services raise domain exceptions; they know nothing about HTTP, because
background jobs call them too. Views do not catch them: this handler, set as
DRF's EXCEPTION_HANDLER, turns them into responses:

- Django's ValidationError: 400, with the errors by field.
- StateConflict: 409. The request conflicts with the current state of the
  resource; the client can refresh it and decide again.
- ActionNotAllowed: 403. The action exists, but not for this user.

Anything else (MissingSlaPolicy, for example) is a server error: a 500, logged
by Django with its traceback.

The base classes live here so that core does not import the apps that use them:
apps subclass them (tickets' TransitionNotDefined is a StateConflict).
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback


class DomainError(Exception):
    """A rule of the domain rejected the operation; nothing was changed.

    `detail` is shown to the user, so it is written in Spanish. `extra` adds
    fields to the response body, e.g. the current status in a conflict.
    """

    code = "domain_error"

    def __init__(self, detail: str, **extra):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class StateConflict(DomainError):
    code = "state_conflict"


class ActionNotAllowed(DomainError):
    code = "action_not_allowed"


HTTP_STATUS = {
    StateConflict: status.HTTP_409_CONFLICT,
    ActionNotAllowed: status.HTTP_403_FORBIDDEN,
}


def exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(detail=_validation_detail(exc))
    for base, http_status in HTTP_STATUS.items():
        if isinstance(exc, base):
            # As DRF does for its own exceptions: the ATOMIC_REQUESTS transaction
            # must not commit whatever the view did before the error.
            set_rollback()
            return Response(
                {"detail": exc.detail, "code": exc.code, **exc.extra}, status=http_status
            )
    # DRF's handler builds the response and, for handled exceptions, marks the
    # transaction for rollback too.
    return drf_exception_handler(exc, context)


def _validation_detail(exc: DjangoValidationError):
    if hasattr(exc, "error_dict"):
        return exc.message_dict
    # Errors not tied to a field, under the same key DRF's serializers use.
    return {api_settings.NON_FIELD_ERRORS_KEY: exc.messages}
