"""Python SDK for the January AI nutrition intelligence API.

One API for understanding what people eat and how food may affect them: recognize food from a
photo or a sentence, search a food database by name or barcode, keep a per-user food diary, and
predict a glucose response to a meal without a sensor.

Example:
    Recognize the food in a photo, with ``JANUARY_API_KEY`` set in the environment::

        from january_ai import January

        client = January()
        scan = client.food_scans.scan_photo("lunch.jpg")
        for detection in scan.detections:
            print(detection.food.name)

:class:`January` is the blocking client and :class:`AsyncJanuary` its asyncio and trio counterpart;
the two are identical apart from ``await``. Both take the API key from the ``JANUARY_API_KEY``
environment variable when one is not passed, and both reach the API through seven resource groups:
``auth``, ``credits``, ``foods``, ``restaurants``, ``food_scans``, ``food_logs``, and ``glucose``.

Everything that goes wrong once a request is under way derives from :class:`JanuaryError`, as does
a missing API key, so a single ``except JanuaryError`` covers transport failures and error
responses; the subclasses below let you single out the ones worth handling differently, such as
:class:`RateLimitError` and :class:`CreditLimitExceededError`. Mistakes in the arguments you pass
raise the standard Python exceptions instead - ``ValueError``, ``TypeError``, and
``FileNotFoundError`` for an image path that does not exist - so a scan of a user-supplied file
wants a handler for those too.

Response models and request shapes are not exported here. They live in :mod:`january_ai.types`,
named exactly as the API reference names them::

    from january_ai import types

This release targets API version ``/v1.2``.
"""

from __future__ import annotations

from . import types as types
from ._client import AsyncJanuary, January
from ._exceptions import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    CreditLimitExceededError,
    InternalServerError,
    JanuaryError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
)
from ._images import prepare_image
from ._types import NOT_GIVEN, NotGiven
from ._version import __version__

__all__ = [
    "NOT_GIVEN",
    "APIConnectionError",
    "APIResponseValidationError",
    "APIStatusError",
    "APITimeoutError",
    "AsyncJanuary",
    "AuthenticationError",
    "BadRequestError",
    "CreditLimitExceededError",
    "InternalServerError",
    "January",
    "JanuaryError",
    "NotFoundError",
    "NotGiven",
    "PayloadTooLargeError",
    "PermissionDeniedError",
    "RateLimitError",
    "__version__",
    "prepare_image",
]
