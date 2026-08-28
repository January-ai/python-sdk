"""Predicting the glucose curve a meal produces.

One operation, and almost all of its surface is input serialization: three separate places carry an
instant, and every one of them is refused when it arrives without a timezone.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import httpx
import pytest
import respx

from january_ai import AsyncJanuary, January
from january_ai.types import (
    CgmReadingParam,
    ConsumedFoodEntryParam,
    FoodSelectionParam,
    GlucosePredictionResponse,
    GlucoseUserProfileParam,
)

BASE_URL = "https://partners.january.ai"
PREDICTIONS_URL = f"{BASE_URL}/v1.2/glucose/predictions"

USER_PROFILE: GlucoseUserProfileParam = {
    "age": 42,
    "sex": "female",
    "height": {"value": 66, "unit": "in"},
    "weight": {"value": 150, "unit": "lb"},
    "activity_level": "lightly_active",
    "health_conditions": ["prediabetes"],
}
USER_PROFILE_JSON = {
    "age": 42,
    "sex": "female",
    "height": {"value": 66, "unit": "in"},
    "weight": {"value": 150, "unit": "lb"},
    "activity_level": "lightly_active",
    "health_conditions": ["prediabetes"],
}

FOODS: list[FoodSelectionParam] = [{"id": 101963552, "serving": {"id": 68051535, "quantity": 1.4}}]
FOODS_JSON = [{"id": 101963552, "serving": {"id": 68051535, "quantity": 1.4}}]

START_TIME = datetime(2024, 9, 13, 8, 0, tzinfo=timezone.utc)

CGM_DATA: list[CgmReadingParam] = [
    {"timestamp": datetime(2024, 9, 10, 8, 15, tzinfo=timezone.utc), "value": 104},
    {"timestamp": "2024-09-10T08:30:00Z", "value": 112},
]
CONSUMED_FOODS: list[ConsumedFoodEntryParam] = [
    {
        "timestamp": datetime(2024, 9, 10, 8, 0, tzinfo=timezone.utc),
        "id": 101963552,
        "serving": {"id": 68051535, "quantity": 1},
    }
]

PREDICTION_PAYLOAD = {
    "prediction": [
        {"minutes": 0, "value": 95},
        {"minutes": 15, "value": 118},
        {"minutes": 30, "value": 140},
    ],
    "impact_score": "low",
    "chart": {"min": 70, "max": 140},
}


def _fingerprint(request: httpx.Request) -> tuple[str, str, bytes]:
    """Reduce a request to the parts the synchronous and asynchronous clients must agree on."""
    return (request.method, str(request.url), request.content)


@pytest.mark.anyio
async def test_predict_posts_the_profile_the_meal_and_the_history(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Render every instant as ISO 8601, including the ones nested in the history arrays."""
    route = respx_mock.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json=PREDICTION_PAYLOAD)
    )

    prediction = client.glucose.predict(
        user_profile=USER_PROFILE,
        foods=FOODS,
        start_time=START_TIME,
        cgm_data=CGM_DATA,
        consumed_foods=CONSUMED_FOODS,
        end_user_id="acme-user-8271",
        end_user_timezone="America/New_York",
    )

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path == "/v1.2/glucose/predictions"
    assert not request.url.params
    assert json.loads(request.content) == {
        "user_profile": USER_PROFILE_JSON,
        "foods": FOODS_JSON,
        "start_time": "2024-09-13T08:00:00+00:00",
        "cgm_data": [
            {"timestamp": "2024-09-10T08:15:00+00:00", "value": 104},
            {"timestamp": "2024-09-10T08:30:00Z", "value": 112},
        ],
        "consumed_foods": [
            {
                "timestamp": "2024-09-10T08:00:00+00:00",
                "id": 101963552,
                "serving": {"id": 68051535, "quantity": 1},
            }
        ],
    }
    assert request.headers["x-end-user-id"] == "acme-user-8271"
    assert request.headers["x-end-user-timezone"] == "America/New_York"

    assert isinstance(prediction, GlucosePredictionResponse)
    assert prediction.impact_score == "low"
    assert prediction.prediction[2].minutes == 30
    assert prediction.prediction[2].value == 140.0
    assert prediction.chart.min == 70.0
    assert prediction.chart.max == 140.0

    await async_client.glucose.predict(
        user_profile=USER_PROFILE,
        foods=FOODS,
        start_time=START_TIME,
        cgm_data=CGM_DATA,
        consumed_foods=CONSUMED_FOODS,
        end_user_id="acme-user-8271",
        end_user_timezone="America/New_York",
    )
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_predict_omits_the_history_when_it_is_not_given(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Send neither history array when neither was supplied, rather than sending nulls."""
    route = respx_mock.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json=PREDICTION_PAYLOAD)
    )

    client.glucose.predict(
        user_profile=USER_PROFILE, foods=FOODS, start_time="2024-09-13T08:00:00Z"
    )

    request = route.calls.last.request
    assert json.loads(request.content) == {
        "user_profile": USER_PROFILE_JSON,
        "foods": FOODS_JSON,
        "start_time": "2024-09-13T08:00:00Z",
    }
    assert "x-end-user-timezone" not in request.headers


def test_predict_rejects_a_naive_start_time(client: January, respx_mock: respx.MockRouter) -> None:
    """Refuse a naive ``start_time``: the whole curve is anchored to it."""
    respx_mock.post(PREDICTIONS_URL).mock(return_value=httpx.Response(200, json=PREDICTION_PAYLOAD))

    with pytest.raises(ValueError, match="start_time requires a timezone-aware datetime"):
        client.glucose.predict(
            user_profile=USER_PROFILE, foods=FOODS, start_time=datetime(2024, 9, 13, 8, 0)
        )

    assert respx_mock.calls.call_count == 0


def test_predict_rejects_a_naive_cgm_timestamp(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Refuse a naive reading timestamp and name the entry that carried it."""
    respx_mock.post(PREDICTIONS_URL).mock(return_value=httpx.Response(200, json=PREDICTION_PAYLOAD))
    naive: list[CgmReadingParam] = [
        {"timestamp": datetime(2024, 9, 10, 8, 15, tzinfo=timezone.utc), "value": 104},
        {"timestamp": datetime(2024, 9, 10, 8, 30), "value": 112},
    ]

    with pytest.raises(ValueError, match=r"cgm_data\[1\].timestamp"):
        client.glucose.predict(
            user_profile=USER_PROFILE,
            foods=FOODS,
            start_time=START_TIME,
            cgm_data=naive,
            consumed_foods=CONSUMED_FOODS,
        )

    assert respx_mock.calls.call_count == 0


def test_predict_rejects_a_naive_consumed_food_timestamp(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Refuse a naive history-meal timestamp for the same reason."""
    respx_mock.post(PREDICTIONS_URL).mock(return_value=httpx.Response(200, json=PREDICTION_PAYLOAD))
    naive: list[ConsumedFoodEntryParam] = [
        {
            "timestamp": datetime(2024, 9, 10, 8, 0),
            "id": 101963552,
            "serving": {"id": 68051535, "quantity": 1},
        }
    ]

    with pytest.raises(ValueError, match=r"consumed_foods\[0\].timestamp"):
        client.glucose.predict(
            user_profile=USER_PROFILE,
            foods=FOODS,
            start_time=START_TIME,
            cgm_data=CGM_DATA,
            consumed_foods=naive,
        )

    assert respx_mock.calls.call_count == 0


def test_predict_rejects_a_date_as_start_time(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Refuse a ``date`` by name instead of failing on ``.tzinfo`` deep inside serialization."""
    respx_mock.post(PREDICTIONS_URL).mock(return_value=httpx.Response(200, json=PREDICTION_PAYLOAD))

    with pytest.raises(TypeError) as caught:
        client.glucose.predict(
            user_profile=USER_PROFILE,
            foods=FOODS,
            start_time=date(2024, 9, 13),  # type: ignore[arg-type]
        )

    assert "start_time" in str(caught.value)
    assert "got date" in str(caught.value)
    assert respx_mock.calls.call_count == 0


def test_predict_rejects_a_date_inside_the_cgm_history(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Name the offending entry rather than letting ``json.dumps`` refuse the ``date`` anonymously.

    Only ``datetime`` and ``str`` used to be converted, so a ``date`` fell through untouched and
    surfaced as ``TypeError: Object of type date is not JSON serializable`` - which names neither
    the parameter nor the entry it came from.
    """
    respx_mock.post(PREDICTIONS_URL).mock(return_value=httpx.Response(200, json=PREDICTION_PAYLOAD))
    dated: list[CgmReadingParam] = [
        {"timestamp": datetime(2024, 9, 10, 8, 15, tzinfo=timezone.utc), "value": 104},
        {"timestamp": date(2024, 9, 10), "value": 112},  # type: ignore[typeddict-item]
    ]

    with pytest.raises(TypeError, match=r"cgm_data\[1\].timestamp"):
        client.glucose.predict(
            user_profile=USER_PROFILE,
            foods=FOODS,
            start_time=START_TIME,
            cgm_data=dated,
            consumed_foods=CONSUMED_FOODS,
        )

    assert respx_mock.calls.call_count == 0


def test_predict_rejects_a_date_inside_the_consumed_food_history(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Do the same for the other history list, which shares the conversion."""
    respx_mock.post(PREDICTIONS_URL).mock(return_value=httpx.Response(200, json=PREDICTION_PAYLOAD))
    dated: list[ConsumedFoodEntryParam] = [
        {
            "timestamp": date(2024, 9, 10),  # type: ignore[typeddict-item]
            "id": 101963552,
            "serving": {"id": 68051535, "quantity": 1},
        }
    ]

    with pytest.raises(TypeError, match=r"consumed_foods\[0\].timestamp"):
        client.glucose.predict(
            user_profile=USER_PROFILE,
            foods=FOODS,
            start_time=START_TIME,
            cgm_data=CGM_DATA,
            consumed_foods=dated,
        )

    assert respx_mock.calls.call_count == 0


def test_predict_forwards_a_history_entry_with_no_timestamp(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Leave "is a timestamp required?" to the server, which answers with a 400 that names it."""
    route = respx_mock.post(PREDICTIONS_URL).mock(
        return_value=httpx.Response(200, json=PREDICTION_PAYLOAD)
    )

    client.glucose.predict(
        user_profile=USER_PROFILE,
        foods=FOODS,
        start_time=START_TIME,
        cgm_data=[{"value": 104}],  # type: ignore[typeddict-item]
        consumed_foods=CONSUMED_FOODS,
    )

    assert json.loads(route.calls.last.request.content)["cgm_data"] == [{"value": 104}]


def test_predict_leaves_the_caller_history_unmodified(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Copy the history entries rather than rewriting the caller's own dictionaries in place."""
    respx_mock.post(PREDICTIONS_URL).mock(return_value=httpx.Response(200, json=PREDICTION_PAYLOAD))
    readings: list[CgmReadingParam] = [
        {"timestamp": datetime(2024, 9, 10, 8, 15, tzinfo=timezone.utc), "value": 104}
    ]

    client.glucose.predict(
        user_profile=USER_PROFILE,
        foods=FOODS,
        start_time=START_TIME,
        cgm_data=readings,
        consumed_foods=CONSUMED_FOODS,
    )

    assert readings[0]["timestamp"] == datetime(2024, 9, 10, 8, 15, tzinfo=timezone.utc)
