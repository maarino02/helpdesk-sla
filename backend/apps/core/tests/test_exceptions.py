from django.core.exceptions import ValidationError
from rest_framework.exceptions import NotFound

from apps.core.exceptions import exception_handler


def test_field_errors_keep_their_fields():
    response = exception_handler(ValidationError({"category": "Inactiva."}), {})

    assert response.status_code == 400
    assert response.data == {"category": ["Inactiva."]}


def test_errors_without_a_field_use_the_non_field_key():
    response = exception_handler(ValidationError("No se puede."), {})

    assert response.status_code == 400
    assert response.data == {"non_field_errors": ["No se puede."]}


def test_drf_exceptions_are_handled_as_usual():
    assert exception_handler(NotFound(), {}).status_code == 404


def test_unknown_exceptions_are_left_to_django():
    # None: DRF re-raises it and Django answers 500 and logs the traceback.
    assert exception_handler(RuntimeError("bug"), {}) is None
