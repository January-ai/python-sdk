"""The exception hierarchy and the error-response parsing that builds it.

Everything that goes wrong once a request is under way derives from :class:`JanuaryError`, as does
a missing API key, so one ``except JanuaryError`` catches transport failures, error responses, and
a response body that did not match its schema. Below it the tree splits in three:
:class:`APIConnectionError` for requests that never produced a response, :class:`APIStatusError`
for responses the API returned with a 4xx or 5xx status, and
:class:`APIResponseValidationError` for a success response whose body was not the expected shape.

Mistakes in the arguments a caller passes are *not* in this hierarchy: they raise the standard
Python exceptions instead - ``ValueError`` (a naive ``datetime``, a missing ``end_user_id``, an
``end_user_id`` or ``end_user_timezone`` that is not printable ASCII, an image that cannot be
prepared, a negative ``max_retries``), ``TypeError`` (a ``date`` where a timestamp belongs, an
unsupported ``image`` type, or a text-mode file handle), and ``FileNotFoundError`` (an image path
that does not exist).

Status classes are chosen from the error body's ``code`` first and its HTTP status second, because
one status can mean two different things. A 429 is either a rate limit, which clears on its own,
or an exhausted monthly credit allowance, which does not; those become
:class:`RateLimitError` and :class:`CreditLimitExceededError` respectively, and the second is
deliberately *not* a subclass of the first so that a sleep-and-retry handler cannot swallow it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

import httpx

from ._backoff import parse_retry_after

__all__ = [
    "APIConnectionError",
    "APIResponseValidationError",
    "APIStatusError",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "CreditLimitExceededError",
    "InternalServerError",
    "JanuaryError",
    "NotFoundError",
    "PayloadTooLargeError",
    "PermissionDeniedError",
    "RateLimitError",
    "map_exception",
    "parse_error_body",
]

# An error body without a usable message still has to say something; the first 200 characters are
# enough to identify an HTML error page or a proxy's plain-text complaint without flooding a log.
# The same bound applies to a JSON body's own ``message``: a stack trace or a validation report
# echoed back into that field turns str(error) into megabytes that reach every log line and every
# traceback the caller prints.
_MAX_TEXT_MESSAGE_CHARS: Final = 200
_TRUNCATION_MARKER: Final = "... (truncated; the full text is in .body)"


class JanuaryError(Exception):
    """Base class for every error raised by the January SDK.

    Configuration problems detected before any request is sent - a missing API key, an invalid
    ``max_retries`` - are raised as this class directly.
    """


class APIConnectionError(JanuaryError):
    """Raised when a request could not be completed and no response was received.

    The cause is below the HTTP layer: DNS failure, a refused or dropped connection, a TLS error,
    or a protocol violation. Whether the server acted on the request is unknowable, which is why
    the SDK replays these only when the operation is safe to repeat.

    Attributes:
        message: A description of the underlying transport failure.
        request: The request that failed, when one had been built.
    """

    def __init__(self, message: str, *, request: httpx.Request | None = None) -> None:
        """Store the failure description and the request that provoked it."""
        super().__init__(message)
        self.message = message
        self.request = request


class APITimeoutError(APIConnectionError):
    """Raised when a request exceeded its timeout.

    Photo and text scans run model inference server-side and are given a longer timeout than the
    other endpoints; when one still times out, a smaller image usually succeeds.
    """


class APIResponseValidationError(JanuaryError):
    """Raised when a success response body did not match the model this release expects.

    The request succeeded and the body was valid JSON, but a field the schema requires was missing
    or held a value of the wrong shape. In practice this means the API changed, or something
    between this client and the API answered with well-formed JSON of its own. The underlying
    ``pydantic.ValidationError``, which names the offending fields, is kept as ``__cause__``.

    Attributes:
        message: An explanation naming the model that was expected.
        status_code: The HTTP status of the response, which was a success status.
        response: The raw response, so the body can be inspected as the server sent it.
    """

    def __init__(self, message: str, *, response: httpx.Response) -> None:
        """Store the explanation and the response whose body could not be validated."""
        super().__init__(message)
        self.message = message
        self.response = response
        self.status_code = response.status_code


class APIStatusError(JanuaryError):
    """Raised when the API returns a 4xx or 5xx response.

    Also raised directly for statuses the SDK has no dedicated subclass for, so a status added to
    the API after this release still arrives as a typed error rather than a parsing failure.

    Attributes:
        message: The developer-facing explanation of what went wrong and how to fix it.
        status_code: The HTTP status of the response.
        code: The API's stable machine-readable identifier for the class of failure, such as
            ``invalid_request`` or ``rate_limited``. Build conditional logic on this, never on the
            wording of ``message``. ``None`` when the body carried no usable code.
        docs_url: A link to the documentation for this failure, when the API supplied one.
        request_id: The ``x-request-id`` response header, worth quoting in a support request.
        response: The raw response, for headers and anything the SDK did not model.
        body: The parsed JSON error body, or the raw response text when it was not a JSON object.
        retry_after: Seconds the server asked the caller to wait, from the ``Retry-After`` header,
            or ``None`` when it sent none. Read on every status rather than on 429 alone: a 503
            carrying ``Retry-After: 600`` is telling the caller something just as actionable, and
            without this it could only be found by reading the raw headers.
    """

    def __init__(
        self,
        message: str,
        *,
        response: httpx.Response,
        code: str | None,
        docs_url: str | None = None,
        body: object | None = None,
    ) -> None:
        """Build a status error from a response and its already-parsed error body."""
        super().__init__(message)
        self.message = message
        self.response = response
        self.status_code = response.status_code
        self.code = code
        self.docs_url = docs_url
        self.body = body
        self.request_id: str | None = response.headers.get("x-request-id")
        self.retry_after: float | None = parse_retry_after(response.headers.get("retry-after"))
        self._notes: list[str] = []

    def _add_note(self, note: str) -> None:
        """Append an explanation of an SDK-side decision to what ``str()`` renders.

        The API's own ``message`` is left exactly as it arrived, since callers key logs and tests
        off it. A note says what the SDK did with the response - declining to sleep through a long
        ``Retry-After``, for instance - which is otherwise invisible from the raised error.
        """
        self._notes.append(note)

    def __str__(self) -> str:
        """Render the API's message with the status, the code, and any SDK note that applies."""
        if self.code is None:
            rendered = f"{self.message} (status {self.status_code})"
        else:
            rendered = f"{self.message} (status {self.status_code}, code {self.code})"
        if not self._notes:
            return rendered
        return " ".join([rendered, *self._notes])


class BadRequestError(APIStatusError):
    """A 400: a field is missing, malformed, or outside the allowed vocabulary.

    The message names the offending field and, where there is one, the accepted set of values.
    """


class AuthenticationError(APIStatusError):
    """A 401: the ``Authorization`` header is missing, or the credential is invalid or expired.

    Account keys are sent as ``Authorization: Bearer sk-...``; client tokens expire within two
    hours of being minted, so a long-lived session must mint a fresh one.
    """


class PermissionDeniedError(APIStatusError):
    """A 403: the credential is valid but not allowed to make this call.

    Minting client tokens requires the account's ``sk-`` API key and cannot be done with a client
    token, and a client token may only reach endpoints covered by the scopes it was granted.
    """


class NotFoundError(APIStatusError):
    """A 404: no such resource - an unknown food id, an unmatched barcode, or a missing food log.

    Food logs are scoped per end user, so a log id that exists for one user is absent for another.
    """


class PayloadTooLargeError(APIStatusError):
    """A 413: the request body exceeds the 5 MB limit.

    Base64 encoding inflates an image by roughly a third, so keep raw images under about 3.5 MB.
    Letting the SDK preprocess a photo keeps it comfortably inside the limit.
    """


class RateLimitError(APIStatusError):
    """A 429 caused by a rate limit, which clears once the window rolls over.

    A per-day allowance resets 24 hours after the first request in its window. Retrying after
    ``retry_after`` seconds is the correct response; the client already does this automatically
    within its retry budget.

    Attributes:
        retry_after: Seconds to wait, taken from the ``Retry-After`` header, or ``None`` when the
            server did not say. Inherited from :class:`APIStatusError`, which reads the header on
            every status; this is where it matters most and where callers look for it.
    """


class CreditLimitExceededError(APIStatusError):
    """A 429 caused by an exhausted monthly credit allowance.

    Retrying does not help: the allowance returns at the start of the next calendar month, and the
    response carries no ``Retry-After``. Call ``client.credits.get()`` for the balance and reset
    date. This is intentionally not a :class:`RateLimitError`, so a sleep-and-retry handler written
    for rate limits does not silently absorb an exhausted account.
    """


class InternalServerError(APIStatusError):
    """A 5xx: the API or something it depends on failed.

    Usually transient - ``internal_error``, ``upstream_error``, ``service_unavailable``, and
    ``upstream_timeout`` are all safe to retry with backoff. ``not_implemented`` is the exception:
    it is permanent until the feature ships.
    """


# Only codes whose class cannot be inferred from the status belong here. Every other documented
# code agrees with its status, and resolving those by status keeps a server that reports an
# unexpected status/code pairing from being mapped to a class the status contradicts.
_CODE_EXCEPTIONS: Final[Mapping[str, type[APIStatusError]]] = {
    "credit_limit_exceeded": CreditLimitExceededError,
    "rate_limited": RateLimitError,
}

_STATUS_EXCEPTIONS: Final[Mapping[int, type[APIStatusError]]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    413: PayloadTooLargeError,
    429: RateLimitError,
}


def parse_error_body(
    response: httpx.Response,
) -> tuple[str, str | None, str | None, object | None]:
    """Extract the message, code, docs link, and body from an error response.

    The API documents error bodies as ``{message, code, docs_url}``, but a proxy, a load balancer,
    or a gateway between the caller and the API can return HTML or plain text instead. This
    function tolerates all of it and never raises: a body that is not a JSON object becomes a
    message of its first 200 characters, and an empty body becomes ``HTTP <status>``.

    The 200-character bound applies to a well-formed JSON ``message`` too. Nothing stops an upstream
    service from echoing a stack trace or a whole validation report into that field, and an error
    whose ``str()`` runs to megabytes is unreadable wherever it lands. The full value is always
    reachable as ``.body``.

    Args:
        response: The error response, already read.

    Returns:
        A tuple of ``(message, code, docs_url, body)``. ``code`` and ``docs_url`` are ``None``
        unless the body was a JSON object that carried them as strings. ``body`` is the parsed
        object when there was one, the raw text when the body was not a JSON object, and ``None``
        when the body was empty.
    """
    try:
        text = response.text
    except Exception:
        # A response whose content was never read (a streamed body, or one closed on the way out
        # of a retry) has no text to offer; the status alone still makes a usable message.
        text = ""

    stripped = text.strip()
    if not stripped:
        return f"HTTP {response.status_code}", None, None, None

    try:
        payload: object = json.loads(stripped)
    except ValueError:
        return _truncate(stripped), None, None, text

    if not isinstance(payload, dict):
        return _truncate(stripped), None, None, text

    body: Mapping[str, object] = payload
    message = _string_field(body, "message")
    return (
        _truncate(message) if message is not None else f"HTTP {response.status_code}",
        _string_field(body, "code"),
        _string_field(body, "docs_url"),
        body,
    )


def map_exception(response: httpx.Response) -> APIStatusError:
    """Build the most specific exception class for an error response.

    The error ``code`` is consulted before the HTTP status, because the API overloads 429 to mean
    either a rate limit or an exhausted credit allowance and only the code tells them apart. A
    status with no dedicated class - 409, 422, or anything added later - yields a plain
    :class:`APIStatusError` rather than an approximation, and any 5xx yields
    :class:`InternalServerError`.

    Args:
        response: The error response, already read.

    Returns:
        The exception to raise. This function never raises one itself.
    """
    message, code, docs_url, body = parse_error_body(response)

    error_class: type[APIStatusError] | None = None
    if code is not None:
        error_class = _CODE_EXCEPTIONS.get(code)
    if error_class is None:
        error_class = _STATUS_EXCEPTIONS.get(response.status_code)
    if error_class is None and response.status_code >= 500:
        error_class = InternalServerError

    if error_class is None:
        error_class = APIStatusError
    return error_class(message, response=response, code=code, docs_url=docs_url, body=body)


def _truncate(text: str) -> str:
    """Bound a message at :data:`_MAX_TEXT_MESSAGE_CHARS`, marking it when anything was cut."""
    if len(text) <= _MAX_TEXT_MESSAGE_CHARS:
        return text
    return f"{text[:_MAX_TEXT_MESSAGE_CHARS]}{_TRUNCATION_MARKER}"


def _string_field(body: Mapping[str, object], key: str) -> str | None:
    """Return a non-empty string field of an error body, or ``None`` if it is absent or not one."""
    value = body.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None
