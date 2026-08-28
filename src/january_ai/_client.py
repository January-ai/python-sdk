"""The two entry points to the SDK: :class:`January` and :class:`AsyncJanuary`.

Both classes do the same three things. They resolve configuration once, in a fixed order - an
explicit argument, then the matching environment variable, then the SDK default - and validate it
immediately, so a missing API key or a negative retry budget surfaces where the mistake was made
rather than at the first request. They own the HTTP client underneath, unless the caller supplied
one, in which case that client's lifetime stays the caller's business. And they expose the seven
resource groups as plain attributes, which is the whole public API surface:
``client.foods.search(...)``, ``client.food_scans.scan_photo(...)``, and so on.

Everything else - request assembly, retries, deserialization - lives in
:mod:`january_ai._base_client`, which these classes hold rather than inherit from.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import httpx

from ._base_client import AsyncAPIClient, SyncAPIClient
from ._constants import DEFAULT_MAX_RETRIES
from .resources import (
    AsyncAuth,
    AsyncCredits,
    AsyncFoodLogs,
    AsyncFoods,
    AsyncFoodScans,
    AsyncGlucose,
    AsyncRestaurants,
    Auth,
    Credits,
    FoodLogs,
    Foods,
    FoodScans,
    Glucose,
    Restaurants,
)

if TYPE_CHECKING:
    from types import TracebackType

__all__ = ["AsyncJanuary", "January"]


class January:
    """A blocking client for the January AI nutrition intelligence API.

    Configuration resolves in one order throughout: an explicit argument wins, then the matching
    environment variable, then the SDK default. The API key is the one required setting, so a
    client built without one - and without ``JANUARY_API_KEY`` in the environment - raises
    :class:`~january_ai.JanuaryError` at construction rather than at the first request.

    Example:
        With ``JANUARY_API_KEY`` exported, searching the food database is two lines::

            client = January()
            results = client.foods.search("greek yogurt", limit=5)

    The client owns a connection pool, so build one and reuse it for the life of the process
    instead of creating one per call. It is safe to share across threads: nothing about it is
    mutated after construction, and the ``httpx.Client`` underneath is itself thread-safe. Use it
    as a context manager, or call :meth:`close`, to release the pool when you are done.
    :class:`AsyncJanuary` is the counterpart for asyncio and trio, with one difference that
    matters: an async client is bound to the event loop it was created on and must not be shared
    across loops.

    This release targets API version ``/v1.2``. Every request path carries that prefix and the
    models in :mod:`january_ai.types` describe the ``/v1.2`` schemas.

    Attributes:
        auth: Minting and revoking the short-lived client tokens a mobile app authenticates with.
        credits: The credit allowance and consumption of the current billing period.
        foods: Food search, autocomplete, barcode lookup, and healthier alternatives.
        restaurants: Nearby restaurants, and menu-item search across them.
        food_scans: Photo and text food recognition, plus conversational correction of a result.
        food_logs: A per-end-user food diary: create, list by date range, update, and delete.
        glucose: Predicted glucose response to a meal, with or without CGM history.
    """

    auth: Auth
    credits: Credits
    foods: Foods
    restaurants: Restaurants
    food_scans: FoodScans
    food_logs: FoodLogs
    glucose: Glucose

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
        """Build a client, validating its configuration before anything is sent.

        Args:
            api_key: Your account key (``sk-...``) from the Developer Dashboard, or a client token
                (``ct-...``). Falls back to the ``JANUARY_API_KEY`` environment variable.
                Surrounding whitespace is stripped.
            base_url: The API origin, for a staging environment or a proxy of your own. Falls back
                to the ``JANUARY_BASE_URL`` environment variable and then to
                ``https://partners.january.ai``. Trailing slashes are removed.
            timeout: The default timeout in seconds, or an ``httpx.Timeout`` for per-phase control.
                ``None`` selects the SDK default of 60 seconds with a 5-second connect timeout.
                The food-scan endpoints run model inference and use a longer default of their own
                unless a call passes its own timeout.
            max_retries: How many times a failed request may be sent again beyond the first
                attempt. Zero disables retrying.
            default_end_user_id: The end-user identifier to send as ``x-end-user-id`` on calls that
                do not name one. Convenient when a client serves a single user; pass the identifier
                per call when one client serves many.
            default_headers: Headers merged into every request. The SDK's own headers take
                precedence, except that a ``User-Agent`` given here is kept, so you can identify
                your traffic without breaking authentication.
            http_client: An ``httpx.Client`` to send through, for a shared connection pool, a
                proxy, or a custom transport. One passed here belongs to you and is never closed by
                :meth:`close`.

        Raises:
            JanuaryError: If no API key was given and none is in the environment.
            ValueError: If ``max_retries`` is negative, or ``base_url`` is not an http(s)
                origin.
        """
        self._client = SyncAPIClient(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_end_user_id=default_end_user_id,
            default_headers=default_headers,
            http_client=http_client,
        )
        self._closed = False

        self.auth = Auth(self._client)
        self.credits = Credits(self._client)
        self.foods = Foods(self._client)
        self.restaurants = Restaurants(self._client)
        self.food_scans = FoodScans(self._client)
        self.food_logs = FoodLogs(self._client)
        self.glucose = Glucose(self._client)

    def close(self) -> None:
        """Release the connection pool.

        Calling this more than once is harmless. An ``httpx.Client`` you passed to the constructor
        is left open: it is yours, and closing it here would break the rest of your application.
        """
        if self._closed:
            return
        self._closed = True
        self._client.close()

    def __enter__(self) -> January:
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

    def __repr__(self) -> str:
        """Render the client's endpoint and a redacted form of its key.

        The API key is never shown in full, so a client that lands in a log line, a traceback, or
        a notebook transcript does not leak the credential.
        """
        return (
            f"{type(self).__name__}(base_url={str(self._client.base_url)!r}, "
            f"api_key={_redact_api_key(self._client.api_key)!r})"
        )


class AsyncJanuary:
    """An asyncio and trio client for the January AI nutrition intelligence API.

    Identical to :class:`January` in configuration, resources, method names, arguments, and
    results; the methods are coroutines, so porting between the two is a matter of adding
    ``await``. Configuration resolves the same way - explicit argument, then environment variable,
    then SDK default - and a missing API key raises :class:`~january_ai.JanuaryError` at
    construction.

    Example:
        With ``JANUARY_API_KEY`` exported, inside a coroutine::

            client = AsyncJanuary()
            results = await client.foods.search("greek yogurt", limit=5)

    Build one client and reuse it: it owns a connection pool, and a client per call pays for a new
    TLS handshake every time. Unlike the synchronous client, this one is bound to the event loop it
    was created on - its pool and its connections belong to that loop - so create it inside your
    async entry point rather than at import time, and do not share a single instance between loops
    or hand it to another thread running a loop of its own. Use it as an async context manager, or
    call :meth:`aclose`, to release the pool.

    This release targets API version ``/v1.2``. Every request path carries that prefix and the
    models in :mod:`january_ai.types` describe the ``/v1.2`` schemas.

    Attributes:
        auth: Minting and revoking the short-lived client tokens a mobile app authenticates with.
        credits: The credit allowance and consumption of the current billing period.
        foods: Food search, autocomplete, barcode lookup, and healthier alternatives.
        restaurants: Nearby restaurants, and menu-item search across them.
        food_scans: Photo and text food recognition, plus conversational correction of a result.
        food_logs: A per-end-user food diary: create, list by date range, update, and delete.
        glucose: Predicted glucose response to a meal, with or without CGM history.
    """

    auth: AsyncAuth
    credits: AsyncCredits
    foods: AsyncFoods
    restaurants: AsyncRestaurants
    food_scans: AsyncFoodScans
    food_logs: AsyncFoodLogs
    glucose: AsyncGlucose

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
        """Build a client, validating its configuration before anything is sent.

        Args:
            api_key: Your account key (``sk-...``) from the Developer Dashboard, or a client token
                (``ct-...``). Falls back to the ``JANUARY_API_KEY`` environment variable.
                Surrounding whitespace is stripped.
            base_url: The API origin, for a staging environment or a proxy of your own. Falls back
                to the ``JANUARY_BASE_URL`` environment variable and then to
                ``https://partners.january.ai``. Trailing slashes are removed.
            timeout: The default timeout in seconds, or an ``httpx.Timeout`` for per-phase control.
                ``None`` selects the SDK default of 60 seconds with a 5-second connect timeout.
                The food-scan endpoints run model inference and use a longer default of their own
                unless a call passes its own timeout.
            max_retries: How many times a failed request may be sent again beyond the first
                attempt. Zero disables retrying.
            default_end_user_id: The end-user identifier to send as ``x-end-user-id`` on calls that
                do not name one.
            default_headers: Headers merged into every request. The SDK's own headers take
                precedence, except that a ``User-Agent`` given here is kept.
            http_client: An ``httpx.AsyncClient`` to send through. One passed here belongs to you
                and is never closed by :meth:`aclose`.

        Raises:
            JanuaryError: If no API key was given and none is in the environment.
            ValueError: If ``max_retries`` is negative, or ``base_url`` is not an http(s)
                origin.
        """
        self._client = AsyncAPIClient(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_end_user_id=default_end_user_id,
            default_headers=default_headers,
            http_client=http_client,
        )
        self._closed = False

        self.auth = AsyncAuth(self._client)
        self.credits = AsyncCredits(self._client)
        self.foods = AsyncFoods(self._client)
        self.restaurants = AsyncRestaurants(self._client)
        self.food_scans = AsyncFoodScans(self._client)
        self.food_logs = AsyncFoodLogs(self._client)
        self.glucose = AsyncGlucose(self._client)

    async def aclose(self) -> None:
        """Release the connection pool.

        Calling this more than once is harmless. An ``httpx.AsyncClient`` you passed to the
        constructor is left open: it is yours to close.
        """
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()

    async def __aenter__(self) -> AsyncJanuary:
        """Enter an async context manager that closes the client on exit."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client on leaving the context."""
        await self.aclose()

    def __repr__(self) -> str:
        """Render the client's endpoint and a redacted form of its key.

        The API key is never shown in full, so a client that lands in a log line, a traceback, or
        a notebook transcript does not leak the credential.
        """
        return (
            f"{type(self).__name__}(base_url={str(self._client.base_url)!r}, "
            f"api_key={_redact_api_key(self._client.api_key)!r})"
        )


def _redact_api_key(api_key: str) -> str:
    """Reduce a credential to its kind.

    January keys are prefixed - ``sk-`` for an account key, ``ct-`` for a client token - and the
    prefix is the only part worth showing: it tells you which credential a client is using without
    revealing any of it. A key with no prefix is hidden entirely.

    Args:
        api_key: The credential to redact.

    Returns:
        ``"<prefix>-***"``, or ``"***"`` when there is no prefix to show.
    """
    prefix, separator, _ = api_key.partition("-")
    if separator and prefix:
        return f"{prefix}-***"
    return "***"
