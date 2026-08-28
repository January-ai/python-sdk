"""Pure retry mathematics: what to retry, and how long to wait before doing so.

Nothing in this module performs I/O or touches the network. It answers four questions the transport
layer asks on every failed attempt: is this HTTP status plus error code worth retrying, is this
transport exception worth retrying, is it a timeout, and how long should the caller sleep. Keeping
the decisions here makes them table-driven and directly unit-testable.

The API documents ``code`` as a stable machine-readable identifier for the class of failure and
directs clients to build retry logic on it rather than on message wording. Only ``rate_limited``,
``internal_error``, ``upstream_error``, ``service_unavailable``, and ``upstream_timeout`` are safe
to retry with backoff; ``not_implemented`` in particular is permanent until the feature ships. New
codes may be added over time, and an unknown code is treated according to its HTTP status class.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Final, Literal

import httpx

from ._constants import INITIAL_RETRY_DELAY, MAX_RETRY_DELAY

RETRYABLE_CODES: frozenset[str] = frozenset(
    {"rate_limited", "internal_error", "upstream_error", "service_unavailable", "upstream_timeout"}
)
"""Error codes the API documents as safe to retry with backoff."""

NEVER_RETRY_CODES: frozenset[str] = frozenset({"credit_limit_exceeded"})
"""Error codes that must not be retried even when their status class says otherwise.

Credit exhaustion arrives as a 429 but carries no ``Retry-After``; the allowance returns at the
start of the next calendar month, so retrying cannot succeed.
"""

_PERMANENT_CODES: frozenset[str] = frozenset(
    {
        "invalid_request",
        "unauthorized",
        "forbidden",
        "not_found",
        "not_implemented",
        "payload_too_large",
    }
)

KNOWN_CODES: frozenset[str] = RETRYABLE_CODES | NEVER_RETRY_CODES | _PERMANENT_CODES
"""Every error code this SDK release recognizes.

Membership is what separates "known and deliberately not retryable" from "unknown"; only an unknown
or missing code falls through to the HTTP status class.
"""

RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
"""Status codes retried when the response carries no recognized error code."""

TransportErrorKind = Literal["pre_send", "ambiguous", "fatal"]
"""How far a failed request got before the transport raised.

``pre_send`` means the request never reached the server and is always safe to replay.
``ambiguous`` means it may have been received and acted on, so replaying it is safe only for
idempotent operations. ``fatal`` means replaying cannot help.
"""

# Ordered most-specific-first and matched by first hit. httpx's hierarchy overlaps - ConnectTimeout
# is both a TimeoutException and a TransportError, ReadTimeout likewise - so the table must list
# only leaf classes and must never list a base class such as TimeoutException or TransportError,
# which would swallow every entry below it.
_TRANSPORT_ERROR_TABLE: Final[tuple[tuple[type[Exception], TransportErrorKind], ...]] = (
    (httpx.ConnectTimeout, "pre_send"),
    (httpx.PoolTimeout, "pre_send"),
    # A proxy that refuses the CONNECT tunnel or fails the SOCKS handshake raises before the
    # caller's own request is forwarded, so the origin never saw a byte of it.
    (httpx.ProxyError, "pre_send"),
    (httpx.ConnectError, "pre_send"),
    (httpx.ReadTimeout, "ambiguous"),
    (httpx.WriteTimeout, "ambiguous"),
    (httpx.ReadError, "ambiguous"),
    (httpx.WriteError, "ambiguous"),
    (httpx.RemoteProtocolError, "ambiguous"),
)

# The exponent at which the doubling sequence has already reached MAX_RETRY_DELAY. Clamping there
# is arithmetically identical to clamping the product, and it keeps a very large max_retries from
# overflowing the float conversion of 2 ** attempt.
_DELAY_EXPONENT_CAP: Final[int] = max(
    0, math.ceil(math.log2(MAX_RETRY_DELAY / INITIAL_RETRY_DELAY))
)


def should_retry_response(status_code: int, code: str | None) -> bool:
    """Decide whether an error response is worth retrying.

    Codes in :data:`NEVER_RETRY_CODES` win over everything, including a 429 status. A code that is
    known but not retryable (``invalid_request``, ``not_implemented``, ...) also returns ``False``
    even when the status happens to be in :data:`RETRYABLE_STATUSES`. Only an unknown or missing
    code falls through to the status class, which is what the API asks clients to do with codes
    added after this release.

    Args:
        status_code: The HTTP status of the error response.
        code: The ``code`` field of the error body, or ``None`` when absent or unparseable.

    Returns:
        ``True`` when the request should be sent again after a delay.
    """
    if code is not None:
        if code in NEVER_RETRY_CODES:
            return False
        if code in RETRYABLE_CODES:
            return True
        if code in KNOWN_CODES:
            return False
    return status_code in RETRYABLE_STATUSES


def classify_transport_error(exc: Exception) -> TransportErrorKind:
    """Classify a transport-level exception by how far the request got.

    Connection setup failures (``ConnectError``, ``ConnectTimeout``, ``PoolTimeout``,
    ``ProxyError``) are ``pre_send``: the server never saw the request, so replaying it is always
    safe - a proxy rejecting the tunnel is a setup failure like any other, and reaches a client
    that merely has ``HTTPS_PROXY`` set in its environment. Failures that
    can strike after the bytes went out (``ReadTimeout``, ``WriteTimeout``, ``ReadError``,
    ``WriteError``, ``RemoteProtocolError``) are ``ambiguous`` and may only be replayed for
    idempotent operations. Everything else - ``UnsupportedProtocol``, ``InvalidURL``,
    ``TooManyRedirects`` and any non-httpx exception - is ``fatal``.

    Args:
        exc: The exception raised while sending the request.

    Returns:
        One of ``"pre_send"``, ``"ambiguous"``, or ``"fatal"``.
    """
    for exc_type, kind in _TRANSPORT_ERROR_TABLE:
        if isinstance(exc, exc_type):
            return kind
    return "fatal"


def is_timeout_error(exc: Exception) -> bool:
    """Report whether an exception is a timeout rather than another transport failure.

    Args:
        exc: The exception raised while sending the request.

    Returns:
        ``True`` for every ``httpx.TimeoutException``, which the client surfaces as
        ``APITimeoutError`` instead of ``APIConnectionError``.
    """
    return isinstance(exc, httpx.TimeoutException)


def compute_delay(attempt: int, *, rng: random.Random) -> float:
    """Compute the jittered exponential backoff delay before the next attempt.

    The delay is ``min(INITIAL_RETRY_DELAY * 2 ** attempt, MAX_RETRY_DELAY)`` scaled by a random
    factor in ``[0.75, 1.0)``, so concurrent clients that failed together do not retry in lockstep.

    Args:
        attempt: The number of attempts already completed; ``0`` for the first retry.
        rng: The random source, injected so tests can seed it and get deterministic delays.

    Returns:
        The number of seconds to sleep.
    """
    exponent = min(attempt, _DELAY_EXPONENT_CAP)
    ceiling: float = min(INITIAL_RETRY_DELAY * 2.0**exponent, MAX_RETRY_DELAY)
    return ceiling * (0.75 + 0.25 * rng.random())


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header value into a number of seconds.

    Accepts both forms the header allows: a delay in seconds, or an HTTP-date resolved against the
    current UTC time. A date parsed without a timezone is treated as UTC, as the HTTP-date grammar
    requires. Values already in the past clamp to ``0.0``. This function never raises.

    Args:
        value: The raw header value, or ``None`` when the header is absent.

    Returns:
        The wait in seconds, never negative, or ``None`` when the header is absent, empty, or
        unparseable.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        if math.isnan(seconds) or math.isinf(seconds):
            return None
        return max(seconds, 0.0)

    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        # parsedate_to_datetime returns a naive datetime for the "-0000" zone, which HTTP defines
        # as UTC. Subtracting it from an aware "now" would raise TypeError.
        parsed = parsed.replace(tzinfo=timezone.utc)

    try:
        delta = (parsed - datetime.now(timezone.utc)).total_seconds()
    except (OverflowError, ValueError):
        return None
    return max(delta, 0.0)
