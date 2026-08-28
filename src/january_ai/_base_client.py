"""The HTTP transport: request assembly, retrying, and response decoding.

:class:`BaseClient` holds the configuration and every decision that does not touch the network -
how a URL is built, which headers a request carries, how query parameters are rendered, how long to
wait, and how a response becomes a model. :class:`SyncAPIClient` and :class:`AsyncAPIClient` add the
one thing that genuinely cannot be shared between the two worlds: the loop that sends a request,
inspects the outcome, and sleeps before trying again.

The retry policy itself lives in :mod:`january_ai._backoff` and is pure. This module only obeys it:
a request that never reached the server is always replayed, one that may have been acted on is
replayed only when the operation says it is safe to (creating a food log says it is not), and a
``Retry-After`` the server sends is honored up to
:data:`~january_ai._constants.MAX_HONORED_RETRY_AFTER` seconds per wait and
:data:`~january_ai._constants.MAX_TOTAL_RETRY_AFTER_WAIT` seconds summed over one call - beyond
either the caller is told to wait rather than being blocked inside the SDK.

"May have been acted on" covers both ways that can happen. A read timeout or a dropped connection
is the obvious one. A retryable 5xx is the same hazard seen from the other end of the wire: a
gateway answering ``upstream_timeout`` is telling you it forwarded the request to an origin that
may well have committed it. Both are gated on ``spec.retry_ambiguous``. A 429 is the exception,
and is always retried: rate limiting is refused before the handler runs, so no duplicate is
possible.
"""

from __future__ import annotations

import logging
import os
import platform
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from typing import TYPE_CHECKING, Final, TypeVar

import anyio
import httpx
from pydantic import BaseModel, ValidationError

from ._backoff import (
    classify_transport_error,
    compute_delay,
    is_timeout_error,
    should_retry_response,
)
from ._constants import (
    CLIENT_TOKEN_PREFIX,
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    ENV_API_KEY,
    ENV_BASE_URL,
    MAX_HONORED_RETRY_AFTER,
    MAX_TOTAL_RETRY_AFTER_WAIT,
)
from ._exceptions import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    JanuaryError,
    map_exception,
)
from ._serialize import to_json_value
from ._types import NotGiven, RequestSpec, ResponseT
from ._version import __version__

if TYPE_CHECKING:
    from types import TracebackType

__all__ = [
    "AsyncAPIClient",
    "BaseClient",
    "SyncAPIClient",
]

logger = logging.getLogger("january_ai")
"""The SDK's logger. Retry decisions are emitted at ``DEBUG``.

Nothing that could carry a secret or a payload is ever passed to it: no ``Authorization`` header, no
API key, no request body (a photo scan body is a base64 image), and no query string, which on some
endpoints carries the end user's identifier.
"""

_USER_AGENT: Final = (
    f"january-ai-python/{__version__} python/{platform.python_version()} httpx/{httpx.__version__}"
)

_CLOSED_MESSAGE: Final = (
    "This {cls} client has been closed and can no longer send requests. Build a new one, or use "
    "`with {cls}(...) as client:` so it is closed only once you are done with it. (If you passed "
    "your own httpx client, it was closed elsewhere in your application.)"
)

_SyncClientT = TypeVar("_SyncClientT", bound="SyncAPIClient")
_AsyncClientT = TypeVar("_AsyncClientT", bound="AsyncAPIClient")


class BaseClient:
    """Configuration plus every request and response decision that performs no I/O.

    Both the synchronous and asynchronous clients derive from this class, so the two behave
    identically in everything except how they wait.

    Header precedence is deliberate and worth stating outright. Assembly starts from the caller's
    ``default_headers``, then the SDK applies its own - ``Authorization``, ``Accept``,
    ``Content-Type`` on any request that carries a body, ``User-Agent``, ``x-end-user-id``, and
    ``x-end-user-timezone`` - and finally re-applies a ``User-Agent`` the caller supplied. The
    result is that an integrator can brand their traffic, which is genuinely useful, but cannot
    accidentally break authentication or content negotiation by setting a header they did not
    realize the SDK depends on: every body this SDK sends is JSON, so a ``default_headers`` entry
    naming some other ``Content-Type`` would describe the request wrongly to every proxy between
    here and the API rather than change what is sent.

    Attributes:
        api_key: The credential sent as ``Authorization: Bearer``. Either an account key (``sk-``)
            or a client token (``ct-``); the API tells them apart itself.
        base_url: The API origin, normalized without a trailing slash.
        timeout: The default timeout for operations that do not set one of their own.
        max_retries: How many times a failed request may be sent again, beyond the first attempt.
        default_end_user_id: The end-user identifier applied when a call does not name one.
        default_headers: Headers merged into every request, under the SDK's own.
    """

    api_key: str
    base_url: httpx.URL
    timeout: httpx.Timeout
    max_retries: int
    default_end_user_id: str | None
    default_headers: dict[str, str]
    _rng: random.Random

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | httpx.URL | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_end_user_id: str | None = None,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        """Resolve and validate the client configuration.

        Args:
            api_key: The API key. Falls back to the ``JANUARY_API_KEY`` environment variable.
            base_url: The API origin. Falls back to ``JANUARY_BASE_URL`` and then to the production
                endpoint. Trailing slashes are stripped.
            timeout: The default timeout in seconds, or an ``httpx.Timeout`` for per-phase control.
            max_retries: The retry budget per request.
            default_end_user_id: The end-user identifier to apply when a call does not name one.
            default_headers: Headers to merge into every request.

        Raises:
            JanuaryError: If no API key was given and none is in the environment.
            ValueError: If ``max_retries`` is negative, or ``base_url`` is not an http(s)
                origin with a host and no query string or fragment. A path prefix, for a gateway
                fronting the API, is allowed.
        """
        if max_retries < 0:
            raise ValueError(f"max_retries must be zero or greater; got {max_retries}")

        self.api_key = _resolve_api_key(api_key)
        self.base_url = _normalize_base_url(base_url)
        self.timeout = _normalize_timeout(timeout)
        self.max_retries = max_retries
        self.default_end_user_id = default_end_user_id
        self.default_headers = dict(default_headers) if default_headers is not None else {}
        self._rng = random.Random()

    @property
    def is_client_token(self) -> bool:
        """Report whether this client authenticates with a client token rather than an account key.

        Read from the credential's prefix - ``ct-`` for a client token, ``sk-`` for an account key -
        which is the only thing about the value the SDK ever inspects. Authentication itself is the
        API's business; this exists so the resource layer can tell which of its local checks apply,
        and the one that uses it is the food-log ``end_user_id`` requirement: an account key carries
        no user of its own and must name one, while a client token already names its end user and
        may omit the header entirely.

        An unrecognized prefix answers ``False`` on purpose. Guessing "client token" for an unknown
        shape would drop the early, local error that catches the common mistake of forgetting
        ``end_user_id`` with an ``sk-`` key; guessing "account key" only keeps it.

        Returns:
            ``True`` when the configured credential is a ``ct-`` client token.
        """
        return self.api_key.startswith(CLIENT_TOKEN_PREFIX)

    def _build_url(self, path: str) -> httpx.URL:
        """Append an operation path to the base URL.

        Args:
            path: The path including its ``/v1.2`` prefix.

        Returns:
            The absolute URL to request.
        """
        suffix = path if path.startswith("/") else f"/{path}"
        return httpx.URL(f"{self.base_url}{suffix}")

    def _build_headers(self, spec: RequestSpec[ResponseT]) -> dict[str, str]:
        """Assemble the headers for one request.

        The caller's ``default_headers`` are applied first and the SDK's own headers are applied
        over them, so ``Authorization``, ``Accept``, and ``Content-Type`` always describe what the
        SDK is actually doing. The one exception is ``User-Agent``: a value the caller supplied is
        put back afterwards, letting an integrator identify their own traffic.

        ``x-end-user-id`` follows the three-way rule the SDK uses everywhere. Omitted means "use
        the client default", an explicit ``None`` means "send no end user at all" and suppresses
        even a header pinned in ``default_headers``, and a string is sent as given. When nothing
        resolves, any value the caller pinned in ``default_headers`` is left in place.

        Both end-user values are checked here rather than left to httpx, which encodes header
        values as ASCII and raises a ``UnicodeEncodeError`` naming a character position in a string
        the caller never sees. An accented email address and a non-Latin username are ordinary
        inputs, so the constraint is worth stating in the SDK's own words instead.

        Args:
            spec: The request being sent.

        Returns:
            The complete header mapping, with no duplicate names in differing letter case.

        Raises:
            ValueError: If the resolved ``end_user_id`` or ``end_user_timezone`` is not printable
                ASCII, which HTTP header values must be.
        """
        headers = dict(self.default_headers)
        user_agent = _get_header(headers, "User-Agent")

        _set_header(headers, "Authorization", f"Bearer {self.api_key}")
        _set_header(headers, "Accept", "application/json")
        _set_header(headers, "User-Agent", user_agent if user_agent is not None else _USER_AGENT)

        if spec.json_body is not None:
            # Every body this SDK sends is JSON, so the header describing it is the SDK's to set,
            # exactly like Authorization and Accept. Left to httpx it would sit under the caller's
            # default_headers instead, and default_headers={"Content-Type": "text/plain"} would
            # ship a JSON body advertised as text - which the API happens to tolerate today, but
            # which any stricter deployment or proxy in the path is entitled to reject.
            _set_header(headers, "Content-Type", "application/json")

        end_user_id = self._resolve_end_user_id(spec.end_user_id)
        if end_user_id is not None:
            _check_header_value(end_user_id, argument="end_user_id", header="x-end-user-id")
            _set_header(headers, "x-end-user-id", end_user_id)
        elif spec.end_user_id is None:
            _discard_header(headers, "x-end-user-id")

        if spec.end_user_timezone is not None:
            _check_header_value(
                spec.end_user_timezone,
                argument="end_user_timezone",
                header="x-end-user-timezone",
            )
            _set_header(headers, "x-end-user-timezone", spec.end_user_timezone)

        # Content-Type is pinned above on requests that carry a body and deliberately left off the
        # rest: a GET or a DELETE sends nothing, so it has no content to type. httpx would set the
        # header itself for a body, but only where the caller had not already claimed the name.
        return headers

    def _resolve_end_user_id(self, end_user_id: str | NotGiven | None) -> str | None:
        """Apply the client default to an omitted end-user identifier.

        Args:
            end_user_id: The value a call supplied, which may be the omitted sentinel.

        Returns:
            The identifier to send, or ``None`` when the header should not be sent.
        """
        if isinstance(end_user_id, NotGiven):
            return self.default_end_user_id
        return end_user_id

    def _prepare_params(
        self, params: Mapping[str, object] | None
    ) -> dict[str, str | int | float | bool]:
        """Render query parameters into the forms the API expects.

        Unset parameters are dropped rather than sent as empty strings, so an omitted ``limit``
        leaves the server's own default in force. Booleans become ``true``/``false`` rather than
        Python's capitalized ``True``/``False``, and dates and timestamps become ISO 8601. The
        caller's mapping is never modified.

        Args:
            params: The raw parameters from the request spec, or ``None``.

        Returns:
            A new mapping containing only the parameters that should appear in the query string.
        """
        prepared: dict[str, str | int | float | bool] = {}
        if params is None:
            return prepared

        for key, value in params.items():
            if value is None or isinstance(value, NotGiven):
                continue
            if isinstance(value, bool):
                prepared[key] = "true" if value else "false"
            elif isinstance(value, datetime | date):
                prepared[key] = value.isoformat()
            elif isinstance(value, str | int | float):
                prepared[key] = value
            else:
                prepared[key] = str(value)
        return prepared

    def _resolve_timeout(self, spec: RequestSpec[ResponseT]) -> httpx.Timeout:
        """Pick the timeout for one request.

        A per-call timeout wins; otherwise the operation's own default applies, which is how the
        food-scan endpoints get the longer allowance their server-side inference needs; otherwise
        the client's timeout is used.

        Args:
            spec: The request being sent.

        Returns:
            The timeout to attach to every attempt of this request.
        """
        timeout = spec.timeout
        if isinstance(timeout, NotGiven):
            return spec.default_timeout if spec.default_timeout is not None else self.timeout
        if isinstance(timeout, httpx.Timeout):
            return timeout
        return httpx.Timeout(timeout)

    def _process_response(
        self, spec: RequestSpec[ResponseT], response: httpx.Response
    ) -> ResponseT | None:
        """Turn a successful response into the operation's return value.

        Only the operation's own declaration decides whether ``None`` is a legitimate answer. An
        empty body arriving where a model was declared is reported rather than returned, because
        every method that names a ``cast_to`` is annotated to return that model and handing back a
        ``None`` the annotation forbids turns a misbehaving proxy into an ``AttributeError``
        somewhere unrelated. Exactly one operation - revoking client tokens - answers 204 with no
        body, and it declares no ``cast_to`` at all.

        Args:
            spec: The request that produced the response.
            response: A response with a 2xx status, already read.

        Returns:
            The validated model, or ``None`` for an operation that returns no body.

        Raises:
            JanuaryError: If a success response body is empty or is not valid JSON, which means
                something other than the API answered.
            APIResponseValidationError: If the body is valid JSON but not the expected shape.
        """
        if spec.cast_to is None:
            return None

        if not response.content.strip():
            raise JanuaryError(
                f"The API returned a {response.status_code} response with an empty body where a "
                f"{spec.cast_to.__name__} was expected. A proxy or gateway between this client "
                f"and {self.base_url} may have answered instead of the API."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            content_type = response.headers.get("content-type", "unset")
            raise JanuaryError(
                f"The API returned a {response.status_code} response whose body is not valid JSON "
                f"(content-type: {content_type}). A proxy or gateway between this client and "
                f"{self.base_url} may have answered instead of the API."
            ) from exc

        model = spec.cast_to
        if not issubclass(model, BaseModel):
            raise JanuaryError(
                f"Cannot deserialize a response into {model!r}, which is not a pydantic model."
            )
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise APIResponseValidationError(
                f"The API's response to {spec.method} {spec.path} did not match the "
                f"{model.__name__} shape this SDK release expects. The API may have changed - "
                f"upgrade january-ai or report it - or something between this client and "
                f"{self.base_url} may have answered instead. The underlying validation error "
                f"names the fields.",
                response=response,
            ) from exc

    def _redirect_error(self, response: httpx.Response) -> JanuaryError:
        """Explain a redirect rather than treating it as a success with no body.

        The API declares no 3xx status on any operation, and the SDK does not follow redirects, so
        one arriving means the request went somewhere other than the API - most often a ``base_url``
        with the wrong scheme, where an ``http://`` origin bounces to ``https://``.

        Args:
            response: The 3xx response.

        Returns:
            The error to raise, naming both the redirect target and the configured origin.
        """
        location = response.headers.get("location", "an unspecified location")
        return JanuaryError(
            f"The API answered {response.status_code} redirecting to {location}. The SDK does not "
            f"follow redirects; check that base_url ({self.base_url}) is the correct origin and "
            f"scheme."
        )

    def _transport_error(self, exc: Exception, request: httpx.Request) -> APIConnectionError:
        """Wrap a transport failure in the SDK's own exception type.

        Args:
            exc: The exception httpx raised.
            request: The attempt that failed.

        Returns:
            An :class:`~january_ai.APITimeoutError` for a timeout, otherwise an
            :class:`~january_ai.APIConnectionError`.
        """
        message = str(exc) or type(exc).__name__
        if is_timeout_error(exc):
            return APITimeoutError(message, request=request)
        return APIConnectionError(message, request=request)

    def _may_replay_after(self, response: httpx.Response, spec: RequestSpec[ResponseT]) -> bool:
        """Report whether an error response may be sent again for this operation.

        A response came back at all, so the request reached a server that may already have acted on
        it: a 502 or a 504 ``upstream_timeout`` is a gateway reporting that it forwarded the request
        to an origin whose fate it does not know. For a non-idempotent operation that is exactly the
        duplicate-write hazard ``retry_ambiguous`` exists to prevent, and creating a food log twice
        shows the end user their meal twice. A 429 is exempt because rate limiting is refused before
        the handler runs, so nothing can have been written.

        Args:
            response: The error response.
            spec: The request being sent.

        Returns:
            ``True`` when replaying the request cannot duplicate work the server already did.
        """
        return spec.retry_ambiguous or response.status_code == 429

    def _status_retry_delay(
        self, error: APIStatusError, attempt: int, *, honored_total: float
    ) -> float | None:
        """Decide how long to wait before replaying a request the API rejected.

        A ``Retry-After`` the server sent takes precedence over the SDK's own backoff, since the
        server knows when its window rolls over. Two bounds apply to it, and crossing either hands
        the caller the error to schedule for themselves rather than blocking inside one call:
        :data:`~january_ai._constants.MAX_HONORED_RETRY_AFTER` on a single wait, and
        :data:`~january_ai._constants.MAX_TOTAL_RETRY_AFTER_WAIT` on their sum across the attempts
        of one call - without which a server repeating a wait just under the first cap could still
        hold a call for ``max_retries`` times as long. Both raise with a note saying so, since a
        caller who sees only the API's own message cannot tell a refusal to wait from a refusal to
        retry.

        The SDK's own backoff is charged to neither budget. It is already bounded, at
        :data:`~january_ai._constants.MAX_RETRY_DELAY` per wait and a few times that in total, and
        truncating it would cut short exactly the retries that make a transient 5xx survivable.

        Args:
            error: The error built from the response.
            attempt: The number of attempts already completed.
            honored_total: Seconds already slept on the server's instruction during this call.

        Returns:
            The delay in seconds, or ``None`` when the wait is too long to absorb and the error
            should be raised instead.
        """
        retry_after = error.retry_after
        if retry_after is None:
            return compute_delay(attempt, rng=self._rng)
        if retry_after > MAX_HONORED_RETRY_AFTER:
            error._add_note(
                f"The server asked for {retry_after:g}s, longer than the "
                f"{MAX_HONORED_RETRY_AFTER:g}s the SDK will sleep through in one wait, so it did "
                f"not wait: retry after that long yourself (see .retry_after)."
            )
            return None
        if honored_total + retry_after > MAX_TOTAL_RETRY_AFTER_WAIT:
            error._add_note(
                f"The SDK had already waited {honored_total:g}s on this call at the server's "
                f"request and stopped rather than add the {retry_after:g}s it asked for next, "
                f"which would exceed its {MAX_TOTAL_RETRY_AFTER_WAIT:g}s total: retry the call "
                f"yourself after waiting (see .retry_after)."
            )
            return None
        return retry_after

    def _log_status_retry(
        self, request: httpx.Request, error: APIStatusError, delay: float, attempt: int
    ) -> None:
        """Record that an error response is about to be retried."""
        logger.debug(
            "retrying %s %s after %.2fs (status=%s code=%s attempt=%s of %s)",
            request.method,
            _log_target(request.url),
            delay,
            error.status_code,
            error.code,
            attempt + 1,
            self.max_retries,
        )

    def _log_transport_retry(
        self, request: httpx.Request, exc: Exception, delay: float, attempt: int
    ) -> None:
        """Record that a transport failure is about to be retried."""
        logger.debug(
            "retrying %s %s after %.2fs (transport=%s attempt=%s of %s)",
            request.method,
            _log_target(request.url),
            delay,
            type(exc).__name__,
            attempt + 1,
            self.max_retries,
        )


class SyncAPIClient(BaseClient):
    """The blocking transport: sends requests with ``httpx.Client`` and sleeps between retries.

    Attributes:
        _sleep: The function used to wait between attempts. Replaceable on the instance so tests
            can record delays instead of spending them.
    """

    _client: httpx.Client
    _sleep: Callable[[float], None]

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | httpx.URL | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_end_user_id: str | None = None,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Build the client and, unless one was supplied, the HTTP client underneath it.

        Args:
            api_key: The API key. Falls back to the ``JANUARY_API_KEY`` environment variable.
            base_url: The API origin. Falls back to ``JANUARY_BASE_URL``, then to production.
            timeout: The default timeout in seconds, or an ``httpx.Timeout``.
            max_retries: The retry budget per request.
            default_end_user_id: The end-user identifier to apply when a call does not name one.
            default_headers: Headers to merge into every request.
            http_client: An ``httpx.Client`` to send through, for connection pooling, proxies, or
                a custom transport. One passed in here belongs to the caller and is never closed
                by this client.

        Raises:
            JanuaryError: If no API key was given and none is in the environment.
            ValueError: If ``max_retries`` is negative, or ``base_url`` is not an http(s)
                origin with a host and no query string or fragment. A path prefix, for a gateway
                fronting the API, is allowed.
        """
        super().__init__(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_end_user_id=default_end_user_id,
            default_headers=default_headers,
        )
        self._owns_client = http_client is None
        self._client = (
            http_client if http_client is not None else httpx.Client(timeout=self.timeout)
        )
        self._sleep = time.sleep

    def send(self, spec: RequestSpec[ResponseT]) -> ResponseT | None:
        """Send a request, retrying within the client's budget, and deserialize the response.

        Every attempt builds a fresh request, so a retry cannot be affected by state httpx left on
        the previous one. Whether a failure is retried at all is decided by
        :mod:`january_ai._backoff`: an exhausted credit allowance is never retried even though it
        arrives as a 429, and a failure that may already have reached the server - a post-send
        transport error, or a retryable 5xx from a gateway that had forwarded the request - is
        replayed only when ``spec.retry_ambiguous`` allows it.

        Args:
            spec: The request to send.

        Returns:
            The model named by ``spec.cast_to``, or ``None`` when the operation returns no body.

        Raises:
            APIStatusError: If the API returned an error status, as the most specific subclass
                that fits the status and error code.
            APITimeoutError: If the request timed out and could not be retried further.
            APIConnectionError: If the request never produced a response.
            APIResponseValidationError: If a success body did not match the expected model.
            JanuaryError: If the client has been closed, if the API redirected, or if a success
                response body is empty or is not valid JSON.
        """
        if self._client.is_closed:
            raise JanuaryError(_CLOSED_MESSAGE.format(cls="January"))

        url = self._build_url(spec.path)
        params = self._prepare_params(spec.params)
        json_body = to_json_value(spec.json_body) if spec.json_body is not None else None
        timeout = self._resolve_timeout(spec)
        attempt = 0
        honored_total = 0.0

        while True:
            request = self._client.build_request(
                spec.method,
                url,
                params=params,
                json=json_body,
                # Built inline rather than bound to a local: a named local holding the
                # Authorization header would sit on this frame for every traceback raised below,
                # where any tool that captures frame locals - pytest --showlocals, an error
                # reporter - would read the API key straight out of it.
                headers=self._build_headers(spec),
                timeout=timeout,
            )
            try:
                response = self._client.send(request)
            except httpx.HTTPError as exc:
                kind = classify_transport_error(exc)
                retryable = kind == "pre_send" or (kind == "ambiguous" and spec.retry_ambiguous)
                if not retryable or attempt >= self.max_retries:
                    raise self._transport_error(exc, request) from exc
                delay = compute_delay(attempt, rng=self._rng)
                self._log_transport_retry(request, exc, delay, attempt)
                self._sleep(delay)
                attempt += 1
                continue

            if 200 <= response.status_code < 300:
                return self._process_response(spec, response)
            if 300 <= response.status_code < 400:
                raise self._redirect_error(response)

            error = map_exception(response)
            if not should_retry_response(response.status_code, error.code):
                raise error
            if not self._may_replay_after(response, spec):
                raise error
            if attempt >= self.max_retries:
                raise error
            delay_or_none = self._status_retry_delay(error, attempt, honored_total=honored_total)
            if delay_or_none is None:
                raise error
            if error.retry_after is not None:
                # Only a wait the server dictated is charged to the budget; the SDK's own backoff
                # is bounded already and shortening it would not help anyone.
                honored_total += delay_or_none

            response.close()
            self._log_status_retry(request, error, delay_or_none, attempt)
            self._sleep(delay_or_none)
            attempt += 1

    def close(self) -> None:
        """Close the underlying HTTP client and release its connection pool.

        An ``httpx.Client`` the caller supplied is left open: it belongs to them and may be shared
        with the rest of their application.
        """
        if self._owns_client:
            self._client.close()

    def __enter__(self: _SyncClientT) -> _SyncClientT:
        """Enter a context manager that closes the client on exit."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client on leaving the context."""
        self.close()


class AsyncAPIClient(BaseClient):
    """The asyncio transport: sends requests with ``httpx.AsyncClient`` and awaits between retries.

    Attributes:
        _sleep: The coroutine function used to wait between attempts. It defaults to ``anyio.sleep``
            rather than ``asyncio.sleep`` so the client also runs under trio. Replaceable on the
            instance so tests can record delays instead of spending them.
    """

    _client: httpx.AsyncClient
    _sleep: Callable[[float], Awaitable[None]]

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | httpx.URL | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_end_user_id: str | None = None,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Build the client and, unless one was supplied, the HTTP client underneath it.

        Args:
            api_key: The API key. Falls back to the ``JANUARY_API_KEY`` environment variable.
            base_url: The API origin. Falls back to ``JANUARY_BASE_URL``, then to production.
            timeout: The default timeout in seconds, or an ``httpx.Timeout``.
            max_retries: The retry budget per request.
            default_end_user_id: The end-user identifier to apply when a call does not name one.
            default_headers: Headers to merge into every request.
            http_client: An ``httpx.AsyncClient`` to send through. One passed in here belongs to
                the caller and is never closed by this client.

        Raises:
            JanuaryError: If no API key was given and none is in the environment.
            ValueError: If ``max_retries`` is negative, or ``base_url`` is not an http(s)
                origin with a host and no query string or fragment. A path prefix, for a gateway
                fronting the API, is allowed.
        """
        super().__init__(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_end_user_id=default_end_user_id,
            default_headers=default_headers,
        )
        self._owns_client = http_client is None
        self._client = (
            http_client if http_client is not None else httpx.AsyncClient(timeout=self.timeout)
        )
        self._sleep = anyio.sleep

    async def send(self, spec: RequestSpec[ResponseT]) -> ResponseT | None:
        """Send a request, retrying within the client's budget, and deserialize the response.

        Behaves exactly as the synchronous client does, awaiting the send and the backoff instead
        of blocking. Every attempt builds a fresh request.

        Args:
            spec: The request to send.

        Returns:
            The model named by ``spec.cast_to``, or ``None`` when the operation returns no body.

        Raises:
            APIStatusError: If the API returned an error status, as the most specific subclass
                that fits the status and error code.
            APITimeoutError: If the request timed out and could not be retried further.
            APIConnectionError: If the request never produced a response.
            APIResponseValidationError: If a success body did not match the expected model.
            JanuaryError: If the client has been closed, if the API redirected, or if a success
                response body is empty or is not valid JSON.
        """
        if self._client.is_closed:
            raise JanuaryError(_CLOSED_MESSAGE.format(cls="AsyncJanuary"))

        url = self._build_url(spec.path)
        params = self._prepare_params(spec.params)
        json_body = to_json_value(spec.json_body) if spec.json_body is not None else None
        timeout = self._resolve_timeout(spec)
        attempt = 0
        honored_total = 0.0

        while True:
            request = self._client.build_request(
                spec.method,
                url,
                params=params,
                json=json_body,
                # Built inline rather than bound to a local, so the Authorization header does not
                # sit on the frame that raises. See the note in SyncAPIClient.send.
                headers=self._build_headers(spec),
                timeout=timeout,
            )
            try:
                response = await self._client.send(request)
            except httpx.HTTPError as exc:
                kind = classify_transport_error(exc)
                retryable = kind == "pre_send" or (kind == "ambiguous" and spec.retry_ambiguous)
                if not retryable or attempt >= self.max_retries:
                    raise self._transport_error(exc, request) from exc
                delay = compute_delay(attempt, rng=self._rng)
                self._log_transport_retry(request, exc, delay, attempt)
                await self._sleep(delay)
                attempt += 1
                continue

            if 200 <= response.status_code < 300:
                return self._process_response(spec, response)
            if 300 <= response.status_code < 400:
                raise self._redirect_error(response)

            error = map_exception(response)
            if not should_retry_response(response.status_code, error.code):
                raise error
            if not self._may_replay_after(response, spec):
                raise error
            if attempt >= self.max_retries:
                raise error
            delay_or_none = self._status_retry_delay(error, attempt, honored_total=honored_total)
            if delay_or_none is None:
                raise error
            if error.retry_after is not None:
                # Only a wait the server dictated is charged to the budget. See SyncAPIClient.send.
                honored_total += delay_or_none

            await response.aclose()
            self._log_status_retry(request, error, delay_or_none, attempt)
            await self._sleep(delay_or_none)
            attempt += 1

    async def aclose(self) -> None:
        """Close the underlying HTTP client and release its connection pool.

        An ``httpx.AsyncClient`` the caller supplied is left open: it belongs to them.
        """
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self: _AsyncClientT) -> _AsyncClientT:
        """Enter a context manager that closes the client on exit."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client on leaving the context."""
        await self.aclose()


def _resolve_api_key(api_key: str | None) -> str:
    """Resolve the API key from the argument or the environment.

    Args:
        api_key: The key passed to the constructor, if any.

    Returns:
        The key with surrounding whitespace removed.

    Raises:
        JanuaryError: If neither source supplied a non-blank key.
    """
    candidate = api_key if api_key is not None else os.environ.get(ENV_API_KEY)
    if candidate is None or not candidate.strip():
        raise JanuaryError(
            "No API key was provided. Pass api_key=... to the client, or set the "
            f"{ENV_API_KEY} environment variable. Account keys (sk-...) are created in the "
            "Developer Dashboard at https://dashboard.january.ai."
        )
    return candidate.strip()


def _normalize_base_url(base_url: str | httpx.URL | None) -> httpx.URL:
    """Resolve the API origin from the argument, the environment, or the production default.

    Every operation path is appended to whatever comes back here, so anything this function lets
    through unexamined becomes a malformed request URL at the first call instead of a message about
    the configuration. Three shapes parse cleanly and then do exactly that, and all three are
    refused: a scheme with no host (``https://``, ``https:///v1``), a query string, and a fragment.
    The query is the worst of them - append ``/v1.2/credits`` to ``https://api.example.com?token=abc``
    and the operation path lands inside the query, so the request goes to the origin's root.

    A path prefix is not one of them and stays allowed: a gateway at
    ``https://proxy.example.com/january`` fronting the API is a legitimate deployment, and the SDK
    appends ``/v1.2/...`` after the prefix. So is a trailing slash, which is stripped.

    Args:
        base_url: The origin passed to the constructor, if any.

    Returns:
        The origin as an ``httpx.URL`` with trailing slashes removed, so paths can be appended
        without producing a double slash.

    Raises:
        ValueError: If the origin names a scheme other than ``http`` or ``https``, or omits one -
            ``january.ai`` with the scheme forgotten would otherwise be accepted and then fail at
            the first request with something that does not point back at the configuration - or if
            it names no host, or carries a query string or a fragment.
    """
    text = str(base_url).strip() if base_url is not None else ""
    if not text:
        text = os.environ.get(ENV_BASE_URL, "").strip()
    if not text:
        text = DEFAULT_BASE_URL

    url = httpx.URL(text.rstrip("/"))
    if url.scheme not in ("http", "https"):
        raise ValueError(
            f"base_url must be an http(s) origin such as https://partners.january.ai; got {text!r}."
        )
    if not url.host:
        raise ValueError(
            f"base_url names no host, so no request can be addressed with it. Pass an origin such "
            f"as https://partners.january.ai, optionally with a path prefix "
            f"(https://proxy.example.com/january); got {text!r}."
        )
    if url.query:
        raise ValueError(
            f"base_url must not carry a query string: the SDK appends the operation path to it, "
            f"which would land inside the query rather than after the host. Pass an origin such "
            f"as https://partners.january.ai, optionally with a path prefix "
            f"(https://proxy.example.com/january), and send per-request parameters through the "
            f"operation instead; got {text!r}."
        )
    if url.fragment:
        raise ValueError(
            f"base_url must not carry a fragment: it is never sent to a server, and the SDK "
            f"appends the operation path after it. Pass an origin such as "
            f"https://partners.january.ai, optionally with a path prefix "
            f"(https://proxy.example.com/january); got {text!r}."
        )
    return url


def _normalize_timeout(timeout: float | httpx.Timeout | None) -> httpx.Timeout:
    """Coerce a timeout argument into an ``httpx.Timeout``.

    Args:
        timeout: Seconds applied to every phase, a fully specified ``httpx.Timeout``, or ``None``
            for the SDK default.

    Returns:
        The timeout to use.
    """
    if timeout is None:
        return DEFAULT_TIMEOUT
    if isinstance(timeout, httpx.Timeout):
        return timeout
    return httpx.Timeout(timeout)


def _check_header_value(value: str, *, argument: str, header: str) -> None:
    """Reject a value HTTP cannot carry in a header, naming the argument that supplied it.

    Header values are ASCII, and control characters are forbidden in them because a bare ``\\r\\n``
    would end the header block and let the rest of the value be read as headers of its own. httpx
    enforces both, but its ``UnicodeEncodeError`` names a codec and a character offset, and its
    ``LocalProtocolError`` quotes the whole offending line - neither says which SDK argument to fix.
    The offending character is reported by position and code point rather than by echoing the value,
    which on these two arguments identifies an end user.

    Args:
        value: The resolved header value.
        argument: The SDK parameter the value came from, for the error message.
        header: The header it would be sent as, for the error message.

    Raises:
        ValueError: If the value is not printable ASCII.
    """
    for index, character in enumerate(value):
        if character.isascii() and not (character < " " or character == "\x7f"):
            continue
        kind = "a control character" if character.isascii() else "a non-ASCII character"
        raise ValueError(
            f"{argument} must be printable ASCII: it is sent as the {header} header, and HTTP "
            f"header values cannot carry {kind}. Character {index} is U+{ord(character):04X}. "
            f"Encode or hash the value before passing it if it may contain one."
        )


def _get_header(headers: Mapping[str, str], name: str) -> str | None:
    """Return a header's value, matching the name case-insensitively as HTTP requires."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _discard_header(headers: dict[str, str], name: str) -> None:
    """Remove every spelling of a header name from a plain dict.

    Header names are case-insensitive but dict keys are not, so a caller's ``user-agent`` and the
    SDK's ``User-Agent`` would otherwise both survive and be sent as two headers.
    """
    lowered = name.lower()
    for key in [key for key in headers if key.lower() == lowered]:
        del headers[key]


def _set_header(headers: dict[str, str], name: str, value: str) -> None:
    """Set a header, replacing any value already present under a differently cased name."""
    _discard_header(headers, name)
    headers[name] = value


def _log_target(url: httpx.URL) -> str:
    """Render a URL for a log line without its query string.

    Query parameters carry an end-user identifier on some endpoints, which does not belong in an
    application's logs.
    """
    return str(url.copy_with(query=None, fragment=None))
