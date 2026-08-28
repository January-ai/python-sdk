"""Predict the glucose curve a meal will produce for a given end user.

One operation lives here, and most of its work is preparing input. The API takes every instant as
an ISO 8601 string with a timezone designator, so the timestamps inside ``cgm_data`` and
``consumed_foods`` are converted the same way ``start_time`` is: a caller can hand this module real
``datetime`` objects and a naive one is refused outright rather than being read in a timezone
nobody chose.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast

import httpx

from .._base_client import AsyncAPIClient, SyncAPIClient
from .._constants import API_VERSION_PATH
from .._serialize import to_iso_datetime
from .._types import NOT_GIVEN, NotGiven, RequestSpec
from ..types import (
    CgmReadingParam,
    ConsumedFoodEntryParam,
    FoodSelectionParam,
    GlucosePredictionResponse,
    GlucoseUserProfileParam,
)

__all__ = ["AsyncGlucose", "Glucose"]

_PREDICTIONS_PATH = f"{API_VERSION_PATH}/glucose/predictions"


def _with_iso_timestamps(
    entries: Sequence[Mapping[str, object]], *, field: str
) -> list[dict[str, object]]:
    """Copy a list of timestamped entries, rendering each ``timestamp`` as ISO 8601.

    Only the ``timestamp`` key is touched; every other key is carried across untouched, so a
    forward-compatible entry the caller built from a newer schema still reaches the API intact.

    Every non-``None`` ``timestamp`` goes through :func:`to_iso_datetime`, including a value of a
    type it cannot render. That is deliberate: a ``date`` left alone here reaches ``json.dumps``
    and raises ``Object of type date is not JSON serializable``, which names neither the parameter
    nor the entry it came from. Whether a ``timestamp`` is required at all stays the server's rule,
    so a missing or explicitly ``None`` one is forwarded untouched.

    Args:
        entries: The CGM readings or consumed foods as the caller supplied them.
        field: The parameter name, used to point an error at the offending entry.

    Returns:
        New dictionaries safe to place in a JSON request body.

    Raises:
        ValueError: If an entry's ``timestamp`` is a naive ``datetime``.
        TypeError: If an entry's ``timestamp`` is neither a ``datetime`` nor a ``str``.
    """
    converted: list[dict[str, object]] = []
    for index, entry in enumerate(entries):
        item = dict(entry)
        timestamp = item.get("timestamp")
        if timestamp is not None:
            item["timestamp"] = to_iso_datetime(
                cast("datetime | str", timestamp), field=f"{field}[{index}].timestamp"
            )
        converted.append(item)
    return converted


def _predict_spec(
    *,
    user_profile: GlucoseUserProfileParam,
    foods: Sequence[FoodSelectionParam],
    start_time: datetime | str,
    cgm_data: Sequence[CgmReadingParam] | None,
    consumed_foods: Sequence[ConsumedFoodEntryParam] | None,
    end_user_id: str | NotGiven | None,
    end_user_timezone: str | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[GlucosePredictionResponse]:
    """Describe a glucose prediction, with every instant rendered as ISO 8601."""
    body: dict[str, object] = {
        "user_profile": user_profile,
        "foods": list(foods),
        "start_time": to_iso_datetime(start_time, field="start_time"),
    }
    # cgm_data and consumed_foods require each other, but the server owns that rule: sending one
    # alone earns a 400 that names the missing half, which is more informative than a local guess.
    if cgm_data is not None:
        body["cgm_data"] = _with_iso_timestamps(cgm_data, field="cgm_data")
    if consumed_foods is not None:
        body["consumed_foods"] = _with_iso_timestamps(consumed_foods, field="consumed_foods")
    return RequestSpec(
        method="POST",
        path=_PREDICTIONS_PATH,
        cast_to=GlucosePredictionResponse,
        json_body=body,
        end_user_id=end_user_id,
        end_user_timezone=end_user_timezone,
        timeout=timeout,
    )


class Glucose:
    """Glucose response predictions for a meal.

    Reached as ``client.glucose``. With a client token, this operation needs the ``glucose:read``
    scope.
    """

    def __init__(self, client: SyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    def predict(
        self,
        *,
        user_profile: GlucoseUserProfileParam,
        foods: Sequence[FoodSelectionParam],
        start_time: datetime | str,
        cgm_data: Sequence[CgmReadingParam] | None = None,
        consumed_foods: Sequence[ConsumedFoodEntryParam] | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        end_user_timezone: str | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> GlucosePredictionResponse:
        """Predict the glucose curve a meal will produce for the given profile.

        The prediction is returned at 15-minute intervals starting at ``start_time``, alongside an
        overall impact score and suggested Y-axis bounds for plotting it.

        ``cgm_data`` and ``consumed_foods`` personalize the prediction and go together: each
        requires the other, and the upstream model needs at least five complete days of paired
        history before the personalization takes effect. Send ``end_user_timezone`` with them too,
        since the history is bucketed into the end user's local days; it defaults to UTC otherwise.

        Args:
            user_profile: The end user characteristics the model is conditioned on. ``age``,
                ``sex``, ``height``, and ``weight`` are required.
            foods: The meal to predict for, each food with the serving and quantity consumed. At
                most 100.
            start_time: When the meal is or will be eaten. A ``datetime`` must be timezone-aware; a
                string must carry a timezone designator.
            cgm_data: Continuous glucose monitor history, at most one reading per 15-minute
                window. Requires ``consumed_foods``.
            consumed_foods: The meals eaten during the CGM history. Requires ``cgm_data``.
            end_user_id: The end user this prediction is for. Omitted uses the client's
                ``default_end_user_id``; with no end user at all, the partner account is itself
                the acting user.
            end_user_timezone: The end user's IANA timezone, such as ``America/New_York``.
                Defaults to UTC when omitted, and should be sent whenever ``cgm_data`` is.
            timeout: Override the timeout for this call.

        Returns:
            The predicted curve, the meal's impact score, and the suggested chart bounds.

        Raises:
            ValueError: If ``start_time`` or any entry timestamp is a naive ``datetime``.
            TypeError: If ``start_time`` or any entry timestamp is neither a ``datetime`` nor a
                ``str`` - a ``date`` is the usual mistake, and names a day rather than an instant.
            APIStatusError: If the API rejects the request. A 400 names the offending field and
                the values it accepts, including when only one of ``cgm_data`` and
                ``consumed_foods`` was sent.
        """
        return cast(
            GlucosePredictionResponse,
            self._client.send(
                _predict_spec(
                    user_profile=user_profile,
                    foods=foods,
                    start_time=start_time,
                    cgm_data=cgm_data,
                    consumed_foods=consumed_foods,
                    end_user_id=end_user_id,
                    end_user_timezone=end_user_timezone,
                    timeout=timeout,
                )
            ),
        )


class AsyncGlucose:
    """Glucose response predictions for a meal.

    Reached as ``client.glucose``. With a client token, this operation needs the ``glucose:read``
    scope.
    """

    def __init__(self, client: AsyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    async def predict(
        self,
        *,
        user_profile: GlucoseUserProfileParam,
        foods: Sequence[FoodSelectionParam],
        start_time: datetime | str,
        cgm_data: Sequence[CgmReadingParam] | None = None,
        consumed_foods: Sequence[ConsumedFoodEntryParam] | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        end_user_timezone: str | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> GlucosePredictionResponse:
        """Predict the glucose curve a meal will produce for the given profile.

        The prediction is returned at 15-minute intervals starting at ``start_time``, alongside an
        overall impact score and suggested Y-axis bounds for plotting it.

        ``cgm_data`` and ``consumed_foods`` personalize the prediction and go together: each
        requires the other, and the upstream model needs at least five complete days of paired
        history before the personalization takes effect. Send ``end_user_timezone`` with them too,
        since the history is bucketed into the end user's local days; it defaults to UTC otherwise.

        Args:
            user_profile: The end user characteristics the model is conditioned on. ``age``,
                ``sex``, ``height``, and ``weight`` are required.
            foods: The meal to predict for, each food with the serving and quantity consumed. At
                most 100.
            start_time: When the meal is or will be eaten. A ``datetime`` must be timezone-aware; a
                string must carry a timezone designator.
            cgm_data: Continuous glucose monitor history, at most one reading per 15-minute
                window. Requires ``consumed_foods``.
            consumed_foods: The meals eaten during the CGM history. Requires ``cgm_data``.
            end_user_id: The end user this prediction is for. Omitted uses the client's
                ``default_end_user_id``; with no end user at all, the partner account is itself
                the acting user.
            end_user_timezone: The end user's IANA timezone, such as ``America/New_York``.
                Defaults to UTC when omitted, and should be sent whenever ``cgm_data`` is.
            timeout: Override the timeout for this call.

        Returns:
            The predicted curve, the meal's impact score, and the suggested chart bounds.

        Raises:
            ValueError: If ``start_time`` or any entry timestamp is a naive ``datetime``.
            TypeError: If ``start_time`` or any entry timestamp is neither a ``datetime`` nor a
                ``str`` - a ``date`` is the usual mistake, and names a day rather than an instant.
            APIStatusError: If the API rejects the request. A 400 names the offending field and
                the values it accepts, including when only one of ``cgm_data`` and
                ``consumed_foods`` was sent.
        """
        return cast(
            GlucosePredictionResponse,
            await self._client.send(
                _predict_spec(
                    user_profile=user_profile,
                    foods=foods,
                    start_time=start_time,
                    cgm_data=cgm_data,
                    consumed_foods=consumed_foods,
                    end_user_id=end_user_id,
                    end_user_timezone=end_user_timezone,
                    timeout=timeout,
                )
            ),
        )
