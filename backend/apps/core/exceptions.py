"""Translation of domain exceptions into HTTP responses, in one place.

Services raise Django's ValidationError (they know nothing about DRF); views do
not catch it. This handler turns it into DRF's, which becomes a 400 with the
errors by field. Anything not listed here (MissingSlaPolicy, for example) is a
server error: a 500, logged by Django with its traceback.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions
from rest_framework.settings import api_settings
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(detail=_validation_detail(exc))
    # DRF's handler builds the response and, for handled exceptions, marks the
    # ATOMIC_REQUESTS transaction for rollback.
    return drf_exception_handler(exc, context)


def _validation_detail(exc: DjangoValidationError):
    if hasattr(exc, "error_dict"):
        return exc.message_dict
    # Errors not tied to a field, under the same key DRF's serializers use.
    return {api_settings.NON_FIELD_ERRORS_KEY: exc.messages}
