"""The end user's meal history: create, list, update, and delete food logs.

Every operation here is per-end-user, so the request has to say which one. With an ``sk-`` API key,
which carries no user of its own, that means the ``x-end-user-id`` header on all four operations,
and the SDK checks for it locally and raises before a request is built: a missing identifier is a
mistake in the calling code, and finding that out from a round trip is slower and less clear than
finding it out from a ``ValueError``.

A ``ct-`` client token is the other case. It already names its end user, so the API accepts these
calls with no ``x-end-user-id`` at all - and refuses one that disagrees with the token, as
``end_user_mismatch``. The local check follows the credential rather than applying to everyone, so
a device holding a token can call these endpoints the way the API documents.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import cast
from urllib.parse import quote

import httpx

from .._base_client import AsyncAPIClient, BaseClient, SyncAPIClient
from .._constants import API_VERSION_PATH
from .._serialize import to_iso_date, to_iso_datetime
from .._types import NOT_GIVEN, NotGiven, RequestSpec
from ..types import DeleteFoodLogResponse, FoodLog, FoodLogListResponse, FoodSelectionParam

__all__ = ["AsyncFoodLogs", "FoodLogs"]

_FOOD_LOGS_PATH = f"{API_VERSION_PATH}/food-logs"

_END_USER_ID_REQUIRED = (
    "end_user_id is required for food log operations - pass end_user_id=... or set "
    "default_end_user_id on the client."
)


def _log_path(log_id: str) -> str:
    """Build the path for one log, escaping the id so it stays a single path segment."""
    return f"{_FOOD_LOGS_PATH}/{quote(log_id, safe='')}"


def _require_end_user_id(client: BaseClient, end_user_id: str | NotGiven | None) -> str | None:
    """Resolve the end user for a food log operation, refusing to send an unaddressable request.

    Whether an identifier is genuinely required depends on the credential, which is why this is not
    a flat check. The API's own description of ``x-end-user-id`` on these operations says it is
    "Required with an API key, which carries no user of its own. A client token already names its
    end user, so it may omit this header - but a value that disagrees with the token is refused with
    ``end_user_mismatch``." An ``sk-`` key therefore still gets the local ``ValueError``, which
    catches the common mistake before a round trip; a ``ct-`` token sends no header and the API
    reads the end user out of the token, which is exactly the device-side case client tokens exist
    for. A credential whose prefix is neither is treated as an API key - see
    :attr:`~january_ai._base_client.BaseClient.is_client_token`.

    Args:
        client: The client whose ``default_end_user_id`` fills in an omitted argument, and whose
            credential decides whether an identifier is required at all.
        end_user_id: The value the call supplied, which may be the omitted sentinel.

    Returns:
        The identifier to send in ``x-end-user-id``, or ``None`` to send no such header, which
        only happens on a client token.

    Raises:
        ValueError: If no identifier resolves - the call omitted it and the client has no default,
            or the call passed ``None`` explicitly - and the client authenticates with an API key,
            for which "send no end user" is exactly what these endpoints reject.
    """
    resolved = client.default_end_user_id if isinstance(end_user_id, NotGiven) else end_user_id
    if not resolved:
        if client.is_client_token:
            return None
        raise ValueError(_END_USER_ID_REQUIRED)
    return resolved


def _create_spec(
    client: BaseClient,
    foods: Sequence[FoodSelectionParam],
    *,
    name: str | None,
    timestamp_utc: datetime | str | None,
    end_user_id: str | NotGiven | None,
    end_user_timezone: str | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[FoodLog]:
    """Describe a food log creation."""
    resolved_end_user_id = _require_end_user_id(client, end_user_id)
    body: dict[str, object] = {"foods": list(foods)}
    if timestamp_utc is not None:
        body["timestamp_utc"] = to_iso_datetime(timestamp_utc, field="timestamp_utc")
    if name is not None:
        body["name"] = name
    return RequestSpec(
        method="POST",
        path=_FOOD_LOGS_PATH,
        cast_to=FoodLog,
        json_body=body,
        end_user_id=resolved_end_user_id,
        end_user_timezone=end_user_timezone,
        timeout=timeout,
        # The only operation in the SDK that refuses to replay an ambiguous transport failure:
        # creating a log is not idempotent and the API accepts no idempotency key, so a request
        # that may already have reached the server would log the end user's meal a second time.
        retry_ambiguous=False,
    )


def _list_spec(
    client: BaseClient,
    start: date | datetime | str,
    end: date | datetime | str,
    *,
    end_user_id: str | NotGiven | None,
    end_user_timezone: str | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[FoodLogListResponse]:
    """Describe a food log listing over a range of calendar days."""
    resolved_end_user_id = _require_end_user_id(client, end_user_id)
    return RequestSpec(
        method="GET",
        path=_FOOD_LOGS_PATH,
        cast_to=FoodLogListResponse,
        # The endpoint takes calendar days (2024-09-01), not timestamps: a datetime contributes
        # only its date part.
        params={
            "start": to_iso_date(start, field="start"),
            "end": to_iso_date(end, field="end"),
        },
        end_user_id=resolved_end_user_id,
        end_user_timezone=end_user_timezone,
        timeout=timeout,
    )


def _update_spec(
    client: BaseClient,
    log_id: str,
    *,
    foods: Sequence[FoodSelectionParam] | NotGiven,
    name: str | NotGiven | None,
    timestamp_utc: datetime | str | NotGiven,
    end_user_id: str | NotGiven | None,
    end_user_timezone: str | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[FoodLog]:
    """Describe a partial update, carrying only the fields the caller actually named."""
    resolved_end_user_id = _require_end_user_id(client, end_user_id)
    body: dict[str, object] = {}
    if not isinstance(foods, NotGiven):
        body["foods"] = list(foods)
    if not isinstance(timestamp_utc, NotGiven):
        body["timestamp_utc"] = to_iso_datetime(timestamp_utc, field="timestamp_utc")
    if not isinstance(name, NotGiven):
        body["name"] = name
    return RequestSpec(
        method="PATCH",
        path=_log_path(log_id),
        cast_to=FoodLog,
        json_body=body,
        end_user_id=resolved_end_user_id,
        end_user_timezone=end_user_timezone,
        timeout=timeout,
    )


def _delete_spec(
    client: BaseClient,
    log_id: str,
    *,
    end_user_id: str | NotGiven | None,
    end_user_timezone: str | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[DeleteFoodLogResponse]:
    """Describe a food log deletion."""
    resolved_end_user_id = _require_end_user_id(client, end_user_id)
    return RequestSpec(
        method="DELETE",
        path=_log_path(log_id),
        cast_to=DeleteFoodLogResponse,
        end_user_id=resolved_end_user_id,
        end_user_timezone=end_user_timezone,
        timeout=timeout,
    )


class FoodLogs:
    """An end user's logged meals: create, list, update, and delete.

    Reached as ``client.food_logs``. Every operation names an end user, either through
    ``end_user_id`` or through the client's ``default_end_user_id``; with an ``sk-`` API key,
    which carries no user of its own, the SDK raises ``ValueError`` when neither supplies one
    rather than sending a request the API would reject.

    A client token is the exception: it already names its end user at mint time, so these calls may
    omit the identifier entirely and the SDK sends no ``x-end-user-id`` header when they do. Passing
    one that disagrees with the token is refused by the API as ``end_user_mismatch``, and passing
    the matching one is fine. Reads with a token need the ``food_logs:read`` scope and writes need
    ``food_logs:write``.
    """

    def __init__(self, client: SyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    def create(
        self,
        foods: Sequence[FoodSelectionParam],
        *,
        name: str | None = None,
        timestamp_utc: datetime | str | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        end_user_timezone: str | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodLog:
        """Log a meal for an end user.

        Foods are given as food and serving ids from a search, scan, or detection result; the
        response echoes the log back hydrated with full nutrition. Save the returned ``id`` to
        update or delete the log later.

        This is the one operation the SDK will not retry after a transport failure that may
        already have reached the server. Creating a log is not idempotent and the API accepts no
        idempotency key, so a replay risks logging the same meal twice. If a create raises
        :class:`~january_ai.APIConnectionError` or :class:`~january_ai.APITimeoutError`, call
        :meth:`list` for the day before sending it again.

        Args:
            foods: The foods eaten, each with the serving and quantity consumed. At most 100.
            name: A label for the meal, such as "Breakfast". At most 256 characters.
            timestamp_utc: When the meal was eaten. A ``datetime`` must be timezone-aware; any
                ISO 8601 offset is accepted and the log is stored and returned in UTC. Omitted
                means now.
            end_user_id: The end user the log belongs to. Omitted uses the client's
                ``default_end_user_id``; on a client token, resolving to nothing sends no header
                and the API reads the end user out of the token.
            end_user_timezone: The end user's IANA timezone, forwarded upstream for local-day date
                handling.
            timeout: Override the timeout for this call.

        Returns:
            The created log, with every food resolved against the database.

        Raises:
            ValueError: If no end user resolves and this client uses an API key rather than a
                client token, or if ``timestamp_utc`` is a naive ``datetime``.
            APIStatusError: If the API rejects the request.
        """
        return cast(
            FoodLog,
            self._client.send(
                _create_spec(
                    self._client,
                    foods,
                    name=name,
                    timestamp_utc=timestamp_utc,
                    end_user_id=end_user_id,
                    end_user_timezone=end_user_timezone,
                    timeout=timeout,
                )
            ),
        )

    def list(
        self,
        start: date | datetime | str,
        end: date | datetime | str,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        end_user_timezone: str | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodLogListResponse:
        """List an end user's logs between two calendar days, ordered by timestamp.

        The range is expressed in whole UTC days and both ends are inclusive: ``start`` from
        00:00:00 UTC, ``end`` through 23:59:59 UTC, and the two may be equal for a single day. A
        ``datetime`` passed here contributes only its date part, since the endpoint takes dates
        rather than instants.

        Args:
            start: First day of the range, as a ``date``, a ``datetime``, or a ``YYYY-MM-DD``
                string.
            end: Last day of the range, in the same forms. May equal ``start``.
            end_user_id: The end user whose logs to read. Omitted uses the client's
                ``default_end_user_id``; on a client token, resolving to nothing sends no header
                and the API reads the end user out of the token.
            end_user_timezone: The end user's IANA timezone, forwarded upstream for local-day date
                handling.
            timeout: Override the timeout for this call.

        Returns:
            The logs in the range. An empty list is a valid result, not an error.

        Raises:
            ValueError: If no end user resolves and this client uses an API key rather than a
                client token.
            APIStatusError: If the API rejects the request, including a 400 when the range is
                inverted.
        """
        return cast(
            FoodLogListResponse,
            self._client.send(
                _list_spec(
                    self._client,
                    start,
                    end,
                    end_user_id=end_user_id,
                    end_user_timezone=end_user_timezone,
                    timeout=timeout,
                )
            ),
        )

    def update(
        self,
        log_id: str,
        *,
        foods: Sequence[FoodSelectionParam] | NotGiven = NOT_GIVEN,
        name: str | NotGiven | None = NOT_GIVEN,
        timestamp_utc: datetime | str | NotGiven = NOT_GIVEN,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        end_user_timezone: str | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodLog:
        """Replace any subset of a logged meal.

        Only the fields named in the call are sent. An argument left out is absent from the PATCH
        body and the stored value survives; whatever is sent replaces what is stored. ``name`` also
        distinguishes the two ways of saying nothing: leaving it out omits the field, so the
        stored name survives, while passing ``name=None`` sends an explicit JSON ``null``. The API
        does not document how it treats a null ``name``, so confirm the result before relying on it
        to clear a label.

        Args:
            log_id: The id returned when the log was created.
            foods: Replacement foods, each with the serving and quantity consumed. At most 100.
            name: A replacement label. ``None`` sends an explicit JSON ``null`` instead of
                omitting the field. At most 256 characters.
            timestamp_utc: A replacement consumption time. A ``datetime`` must be timezone-aware.
            end_user_id: The end user the log belongs to. Omitted uses the client's
                ``default_end_user_id``; on a client token, resolving to nothing sends no header
                and the API reads the end user out of the token.
            end_user_timezone: The end user's IANA timezone, forwarded upstream for local-day date
                handling.
            timeout: Override the timeout for this call.

        Returns:
            The updated log.

        Raises:
            ValueError: If no end user resolves and this client uses an API key rather than a
                client token, or if ``timestamp_utc`` is a naive ``datetime``.
            APIStatusError: If the API rejects the request, including a 404 when this end user has
                no log with that id.
        """
        return cast(
            FoodLog,
            self._client.send(
                _update_spec(
                    self._client,
                    log_id,
                    foods=foods,
                    name=name,
                    timestamp_utc=timestamp_utc,
                    end_user_id=end_user_id,
                    end_user_timezone=end_user_timezone,
                    timeout=timeout,
                )
            ),
        )

    def delete(
        self,
        log_id: str,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        end_user_timezone: str | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> DeleteFoodLogResponse:
        """Delete a logged meal.

        Deletion is idempotent: an unknown or already-deleted ``log_id`` returns the same success
        response, so retrying is safe.

        Args:
            log_id: The id returned when the log was created.
            end_user_id: The end user the log belongs to. Omitted uses the client's
                ``default_end_user_id``; on a client token, resolving to nothing sends no header
                and the API reads the end user out of the token.
            end_user_timezone: The end user's IANA timezone, forwarded upstream for local-day date
                handling.
            timeout: Override the timeout for this call.

        Returns:
            The deletion status. This endpoint answers 200 with a body rather than 204, so there
            is a value to inspect.

        Raises:
            ValueError: If no end user resolves and this client uses an API key rather than a
                client token.
            APIStatusError: If the API rejects the request, including a 400 when ``log_id`` is not
                a UUID.
        """
        return cast(
            DeleteFoodLogResponse,
            self._client.send(
                _delete_spec(
                    self._client,
                    log_id,
                    end_user_id=end_user_id,
                    end_user_timezone=end_user_timezone,
                    timeout=timeout,
                )
            ),
        )


class AsyncFoodLogs:
    """An end user's logged meals: create, list, update, and delete.

    Reached as ``client.food_logs``. Every operation names an end user, either through
    ``end_user_id`` or through the client's ``default_end_user_id``; with an ``sk-`` API key,
    which carries no user of its own, the SDK raises ``ValueError`` when neither supplies one
    rather than sending a request the API would reject.

    A client token is the exception: it already names its end user at mint time, so these calls may
    omit the identifier entirely and the SDK sends no ``x-end-user-id`` header when they do. Passing
    one that disagrees with the token is refused by the API as ``end_user_mismatch``, and passing
    the matching one is fine. Reads with a token need the ``food_logs:read`` scope and writes need
    ``food_logs:write``.
    """

    def __init__(self, client: AsyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    async def create(
        self,
        foods: Sequence[FoodSelectionParam],
        *,
        name: str | None = None,
        timestamp_utc: datetime | str | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        end_user_timezone: str | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodLog:
        """Log a meal for an end user.

        Foods are given as food and serving ids from a search, scan, or detection result; the
        response echoes the log back hydrated with full nutrition. Save the returned ``id`` to
        update or delete the log later.

        This is the one operation the SDK will not retry after a transport failure that may
        already have reached the server. Creating a log is not idempotent and the API accepts no
        idempotency key, so a replay risks logging the same meal twice. If a create raises
        :class:`~january_ai.APIConnectionError` or :class:`~january_ai.APITimeoutError`, call
        :meth:`list` for the day before sending it again.

        Args:
            foods: The foods eaten, each with the serving and quantity consumed. At most 100.
            name: A label for the meal, such as "Breakfast". At most 256 characters.
            timestamp_utc: When the meal was eaten. A ``datetime`` must be timezone-aware; any
                ISO 8601 offset is accepted and the log is stored and returned in UTC. Omitted
                means now.
            end_user_id: The end user the log belongs to. Omitted uses the client's
                ``default_end_user_id``; on a client token, resolving to nothing sends no header
                and the API reads the end user out of the token.
            end_user_timezone: The end user's IANA timezone, forwarded upstream for local-day date
                handling.
            timeout: Override the timeout for this call.

        Returns:
            The created log, with every food resolved against the database.

        Raises:
            ValueError: If no end user resolves and this client uses an API key rather than a
                client token, or if ``timestamp_utc`` is a naive ``datetime``.
            APIStatusError: If the API rejects the request.
        """
        return cast(
            FoodLog,
            await self._client.send(
                _create_spec(
                    self._client,
                    foods,
                    name=name,
                    timestamp_utc=timestamp_utc,
                    end_user_id=end_user_id,
                    end_user_timezone=end_user_timezone,
                    timeout=timeout,
                )
            ),
        )

    async def list(
        self,
        start: date | datetime | str,
        end: date | datetime | str,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        end_user_timezone: str | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodLogListResponse:
        """List an end user's logs between two calendar days, ordered by timestamp.

        The range is expressed in whole UTC days and both ends are inclusive: ``start`` from
        00:00:00 UTC, ``end`` through 23:59:59 UTC, and the two may be equal for a single day. A
        ``datetime`` passed here contributes only its date part, since the endpoint takes dates
        rather than instants.

        Args:
            start: First day of the range, as a ``date``, a ``datetime``, or a ``YYYY-MM-DD``
                string.
            end: Last day of the range, in the same forms. May equal ``start``.
            end_user_id: The end user whose logs to read. Omitted uses the client's
                ``default_end_user_id``; on a client token, resolving to nothing sends no header
                and the API reads the end user out of the token.
            end_user_timezone: The end user's IANA timezone, forwarded upstream for local-day date
                handling.
            timeout: Override the timeout for this call.

        Returns:
            The logs in the range. An empty list is a valid result, not an error.

        Raises:
            ValueError: If no end user resolves and this client uses an API key rather than a
                client token.
            APIStatusError: If the API rejects the request, including a 400 when the range is
                inverted.
        """
        return cast(
            FoodLogListResponse,
            await self._client.send(
                _list_spec(
                    self._client,
                    start,
                    end,
                    end_user_id=end_user_id,
                    end_user_timezone=end_user_timezone,
                    timeout=timeout,
                )
            ),
        )

    async def update(
        self,
        log_id: str,
        *,
        foods: Sequence[FoodSelectionParam] | NotGiven = NOT_GIVEN,
        name: str | NotGiven | None = NOT_GIVEN,
        timestamp_utc: datetime | str | NotGiven = NOT_GIVEN,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        end_user_timezone: str | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodLog:
        """Replace any subset of a logged meal.

        Only the fields named in the call are sent. An argument left out is absent from the PATCH
        body and the stored value survives; whatever is sent replaces what is stored. ``name`` also
        distinguishes the two ways of saying nothing: leaving it out omits the field, so the
        stored name survives, while passing ``name=None`` sends an explicit JSON ``null``. The API
        does not document how it treats a null ``name``, so confirm the result before relying on it
        to clear a label.

        Args:
            log_id: The id returned when the log was created.
            foods: Replacement foods, each with the serving and quantity consumed. At most 100.
            name: A replacement label. ``None`` sends an explicit JSON ``null`` instead of
                omitting the field. At most 256 characters.
            timestamp_utc: A replacement consumption time. A ``datetime`` must be timezone-aware.
            end_user_id: The end user the log belongs to. Omitted uses the client's
                ``default_end_user_id``; on a client token, resolving to nothing sends no header
                and the API reads the end user out of the token.
            end_user_timezone: The end user's IANA timezone, forwarded upstream for local-day date
                handling.
            timeout: Override the timeout for this call.

        Returns:
            The updated log.

        Raises:
            ValueError: If no end user resolves and this client uses an API key rather than a
                client token, or if ``timestamp_utc`` is a naive ``datetime``.
            APIStatusError: If the API rejects the request, including a 404 when this end user has
                no log with that id.
        """
        return cast(
            FoodLog,
            await self._client.send(
                _update_spec(
                    self._client,
                    log_id,
                    foods=foods,
                    name=name,
                    timestamp_utc=timestamp_utc,
                    end_user_id=end_user_id,
                    end_user_timezone=end_user_timezone,
                    timeout=timeout,
                )
            ),
        )

    async def delete(
        self,
        log_id: str,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        end_user_timezone: str | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> DeleteFoodLogResponse:
        """Delete a logged meal.

        Deletion is idempotent: an unknown or already-deleted ``log_id`` returns the same success
        response, so retrying is safe.

        Args:
            log_id: The id returned when the log was created.
            end_user_id: The end user the log belongs to. Omitted uses the client's
                ``default_end_user_id``; on a client token, resolving to nothing sends no header
                and the API reads the end user out of the token.
            end_user_timezone: The end user's IANA timezone, forwarded upstream for local-day date
                handling.
            timeout: Override the timeout for this call.

        Returns:
            The deletion status. This endpoint answers 200 with a body rather than 204, so there
            is a value to inspect.

        Raises:
            ValueError: If no end user resolves and this client uses an API key rather than a
                client token.
            APIStatusError: If the API rejects the request, including a 400 when ``log_id`` is not
                a UUID.
        """
        return cast(
            DeleteFoodLogResponse,
            await self._client.send(
                _delete_spec(
                    self._client,
                    log_id,
                    end_user_id=end_user_id,
                    end_user_timezone=end_user_timezone,
                    timeout=timeout,
                )
            ),
        )
