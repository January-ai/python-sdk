"""The per-end-user food diary: create, list, update, and delete.

Two things distinguish this resource from the rest. Every operation has to say which end user it
acts for, and with an ``sk-`` API key that means the ``x-end-user-id`` header, which the SDK
enforces locally rather than spending a round trip on it - while a ``ct-`` client token already
names its end user and may send no header at all, so the local check follows the credential. And
``update`` is a PATCH, so it has to tell "leave this field alone" apart from "set this field to
null" - the difference between an omitted argument and an explicit ``None``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timezone

import httpx
import pytest
import respx

from january_ai import AsyncJanuary, January
from january_ai.types import DeleteFoodLogResponse, FoodLog, FoodLogListResponse, FoodSelectionParam

BASE_URL = "https://partners.january.ai"
FOOD_LOGS_URL = f"{BASE_URL}/v1.2/food-logs"
LOG_ID = "78129823-8ba2-4183-b13b-71f0e963c606"
LOG_URL = f"{FOOD_LOGS_URL}/{LOG_ID}"

END_USER_ID = "acme-user-8271"

CLIENT_TOKEN = "ct-4fQr7yNb2KcXm9TvLpZ3wHs6JdRg8AeYuQ1oViB5xCn"
"""A credential of the other kind: bound to one end user, so it may omit ``x-end-user-id``."""

FOODS: list[FoodSelectionParam] = [{"id": 101963552, "serving": {"id": 68051535, "quantity": 1.4}}]
FOODS_JSON = [{"id": 101963552, "serving": {"id": 68051535, "quantity": 1.4}}]

LOGGED_FOOD_PAYLOAD = {
    "id": 101963552,
    "name": "Greek Yogurt, Plain, Whole Milk",
    "brand_name": None,
    "image_url": None,
    "glycemic_index": 11.3,
    "glycemic_load": 1.4,
    "nutrients": {"calories": {"value": 220, "unit": "kcal"}},
    "consumed_serving": {"id": 68051535, "quantity": 1.4},
    "serving_details": {"id": 68051535, "quantity": 1, "unit": "cup", "weight_grams": 245},
}
FOOD_LOG_PAYLOAD = {
    "id": LOG_ID,
    "foods": [LOGGED_FOOD_PAYLOAD],
    "timestamp_utc": "2024-09-13T11:34:56Z",
    "name": "Breakfast",
}
LIST_PAYLOAD = {"total_count": 3, "items": [FOOD_LOG_PAYLOAD]}
DELETE_PAYLOAD = {"status": "deleted"}

EATEN_AT = datetime(2024, 9, 13, 11, 34, 56, tzinfo=timezone.utc)


def _fingerprint(request: httpx.Request) -> tuple[str, str, bytes]:
    """Reduce a request to the parts the synchronous and asynchronous clients must agree on."""
    return (request.method, str(request.url), request.content)


@pytest.mark.anyio
async def test_create_posts_the_meal(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Send the foods, the consumption time, and the label, and parse the hydrated log back."""
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(200, json=FOOD_LOG_PAYLOAD)
    )

    log = client.food_logs.create(
        FOODS,
        name="Breakfast",
        timestamp_utc=EATEN_AT,
        end_user_id=END_USER_ID,
        end_user_timezone="America/New_York",
    )

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path == "/v1.2/food-logs"
    assert not request.url.params
    assert json.loads(request.content) == {
        "foods": FOODS_JSON,
        "timestamp_utc": "2024-09-13T11:34:56+00:00",
        "name": "Breakfast",
    }
    assert request.headers["x-end-user-id"] == END_USER_ID
    assert request.headers["x-end-user-timezone"] == "America/New_York"

    assert isinstance(log, FoodLog)
    assert log.id == LOG_ID
    assert log.name == "Breakfast"
    assert log.timestamp_utc == EATEN_AT
    assert log.foods[0].name == "Greek Yogurt, Plain, Whole Milk"
    assert log.foods[0].consumed_serving.quantity == 1.4
    assert log.foods[0].serving_details.weight_grams == 245.0

    await async_client.food_logs.create(
        FOODS,
        name="Breakfast",
        timestamp_utc=EATEN_AT,
        end_user_id=END_USER_ID,
        end_user_timezone="America/New_York",
    )
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_create_omits_the_optional_fields(client: January, respx_mock: respx.MockRouter) -> None:
    """Send only the foods, letting the API timestamp the log as now and leave it unnamed."""
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(200, json=FOOD_LOG_PAYLOAD)
    )

    client.food_logs.create(FOODS, end_user_id=END_USER_ID)

    request = route.calls.last.request
    assert json.loads(request.content) == {"foods": FOODS_JSON}
    assert "x-end-user-timezone" not in request.headers


def test_create_rejects_a_naive_timestamp(client: January, respx_mock: respx.MockRouter) -> None:
    """Refuse a ``datetime`` with no timezone rather than guess which hour was meant."""
    respx_mock.post(FOOD_LOGS_URL).mock(return_value=httpx.Response(200, json=FOOD_LOG_PAYLOAD))

    with pytest.raises(ValueError, match="timezone-aware datetime"):
        client.food_logs.create(
            FOODS, timestamp_utc=datetime(2024, 9, 13, 11, 34, 56), end_user_id=END_USER_ID
        )

    assert respx_mock.calls.call_count == 0


def test_create_rejects_a_date_as_the_timestamp(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """A ``date`` is the likeliest wrong type here, and used to raise a bare ``AttributeError``.

    ``list`` takes a ``date`` for ``start`` and ``end``, so reaching for one on ``timestamp_utc``
    is a natural slip. It has no ``tzinfo``, which is exactly where the old code went first.
    """
    respx_mock.post(FOOD_LOGS_URL).mock(return_value=httpx.Response(200, json=FOOD_LOG_PAYLOAD))

    with pytest.raises(TypeError) as caught:
        client.food_logs.create(
            FOODS,
            timestamp_utc=date(2024, 9, 1),  # type: ignore[arg-type]
            end_user_id=END_USER_ID,
        )

    message = str(caught.value)
    assert "timestamp_utc" in message
    assert "got date" in message
    assert "datetime.combine" in message
    assert respx_mock.calls.call_count == 0


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2024, 9, 1), date(2024, 9, 8)),
        (
            datetime(2024, 9, 1, 23, 30, tzinfo=timezone.utc),
            datetime(2024, 9, 8, 6, 15, tzinfo=timezone.utc),
        ),
        ("2024-09-01", "2024-09-08"),
    ],
    ids=["date", "datetime", "str"],
)
def test_list_serializes_the_range_as_calendar_days(
    client: January,
    respx_mock: respx.MockRouter,
    start: date | datetime | str,
    end: date | datetime | str,
) -> None:
    """Reduce every accepted form to ``YYYY-MM-DD``, since the endpoint takes days, not instants."""
    route = respx_mock.get(FOOD_LOGS_URL).mock(return_value=httpx.Response(200, json=LIST_PAYLOAD))

    client.food_logs.list(start, end, end_user_id=END_USER_ID)

    assert dict(route.calls.last.request.url.params) == {
        "start": "2024-09-01",
        "end": "2024-09-08",
    }


@pytest.mark.anyio
async def test_list_reads_the_range(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """GET the range with the end user named, and parse the logs in it."""
    route = respx_mock.get(FOOD_LOGS_URL).mock(return_value=httpx.Response(200, json=LIST_PAYLOAD))

    logs = client.food_logs.list(date(2024, 9, 1), date(2024, 9, 8), end_user_id=END_USER_ID)

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v1.2/food-logs"
    assert request.content == b""
    assert request.headers["x-end-user-id"] == END_USER_ID

    assert isinstance(logs, FoodLogListResponse)
    assert logs.total_count == 3
    assert logs.items[0].id == LOG_ID

    await async_client.food_logs.list(date(2024, 9, 1), date(2024, 9, 8), end_user_id=END_USER_ID)
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


@pytest.mark.anyio
async def test_update_sends_only_the_named_fields(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """PATCH just the label, leaving the stored foods and timestamp untouched."""
    route = respx_mock.patch(LOG_URL).mock(return_value=httpx.Response(200, json=FOOD_LOG_PAYLOAD))

    log = client.food_logs.update(LOG_ID, name="Lunch", end_user_id=END_USER_ID)

    request = route.calls.last.request
    assert request.method == "PATCH"
    assert request.url.path == f"/v1.2/food-logs/{LOG_ID}"
    assert json.loads(request.content) == {"name": "Lunch"}
    assert isinstance(log, FoodLog)

    await async_client.food_logs.update(LOG_ID, name="Lunch", end_user_id=END_USER_ID)
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_update_sends_an_explicit_null_when_name_is_none(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Distinguish an explicit ``name=None`` from leaving it alone: it sends JSON ``null``."""
    route = respx_mock.patch(LOG_URL).mock(return_value=httpx.Response(200, json=FOOD_LOG_PAYLOAD))

    client.food_logs.update(LOG_ID, name=None, end_user_id=END_USER_ID)

    body = json.loads(route.calls.last.request.content)
    assert body == {"name": None}
    assert "name" in body


def test_update_sends_an_empty_body_when_nothing_was_named(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Send ``{}`` when every field was omitted, rather than inventing values to send."""
    route = respx_mock.patch(LOG_URL).mock(return_value=httpx.Response(200, json=FOOD_LOG_PAYLOAD))

    client.food_logs.update(LOG_ID, end_user_id=END_USER_ID)

    assert json.loads(route.calls.last.request.content) == {}


def test_update_sends_every_field_when_all_are_named(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Replace foods, timestamp, and label together when all three are supplied."""
    route = respx_mock.patch(LOG_URL).mock(return_value=httpx.Response(200, json=FOOD_LOG_PAYLOAD))

    client.food_logs.update(
        LOG_ID,
        foods=FOODS,
        name="Second breakfast",
        timestamp_utc=EATEN_AT,
        end_user_id=END_USER_ID,
    )

    assert json.loads(route.calls.last.request.content) == {
        "foods": FOODS_JSON,
        "timestamp_utc": "2024-09-13T11:34:56+00:00",
        "name": "Second breakfast",
    }


@pytest.mark.anyio
async def test_delete_returns_the_status_body(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Return the deletion status, since this endpoint answers 200 with a body, not 204."""
    route = respx_mock.delete(LOG_URL).mock(return_value=httpx.Response(200, json=DELETE_PAYLOAD))

    deleted = client.food_logs.delete(LOG_ID, end_user_id=END_USER_ID)

    request = route.calls.last.request
    assert request.method == "DELETE"
    assert request.url.path == f"/v1.2/food-logs/{LOG_ID}"
    assert request.content == b""

    assert deleted is not None
    assert isinstance(deleted, DeleteFoodLogResponse)
    assert deleted.status == "deleted"

    await async_client.food_logs.delete(LOG_ID, end_user_id=END_USER_ID)
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_every_operation_requires_a_resolvable_end_user(
    make_client: Callable[..., January], respx_mock: respx.MockRouter
) -> None:
    """Raise locally on all four operations when neither the call nor the client names a user."""
    with make_client(default_end_user_id=None) as anonymous:
        with pytest.raises(ValueError, match="end_user_id is required"):
            anonymous.food_logs.create(FOODS)
        with pytest.raises(ValueError, match="end_user_id is required"):
            anonymous.food_logs.list("2024-09-01", "2024-09-08")
        with pytest.raises(ValueError, match="end_user_id is required"):
            anonymous.food_logs.update(LOG_ID, name="Lunch")
        with pytest.raises(ValueError, match="end_user_id is required"):
            anonymous.food_logs.delete(LOG_ID)

    assert respx_mock.calls.call_count == 0


def test_an_explicit_none_end_user_is_refused(
    make_client: Callable[..., January], respx_mock: respx.MockRouter
) -> None:
    """Refuse ``end_user_id=None`` too: these endpoints have no anonymous mode to fall back to."""
    with (
        make_client(default_end_user_id=END_USER_ID) as scoped,
        pytest.raises(ValueError, match="end_user_id is required"),
    ):
        scoped.food_logs.delete(LOG_ID, end_user_id=None)

    assert respx_mock.calls.call_count == 0


@pytest.mark.anyio
async def test_async_operations_require_a_resolvable_end_user(
    make_async_client: Callable[..., AsyncJanuary], respx_mock: respx.MockRouter
) -> None:
    """Raise the same way from the asynchronous client, still before any request is built."""
    async with make_async_client(default_end_user_id=None) as anonymous:
        with pytest.raises(ValueError, match="end_user_id is required"):
            await anonymous.food_logs.create(FOODS)
        with pytest.raises(ValueError, match="end_user_id is required"):
            await anonymous.food_logs.list("2024-09-01", "2024-09-08")
        with pytest.raises(ValueError, match="end_user_id is required"):
            await anonymous.food_logs.update(LOG_ID, name="Lunch")
        with pytest.raises(ValueError, match="end_user_id is required"):
            await anonymous.food_logs.delete(LOG_ID)

    assert respx_mock.calls.call_count == 0


def test_a_client_token_may_omit_the_end_user_on_every_operation(
    make_client: Callable[..., January], respx_mock: respx.MockRouter
) -> None:
    """Send all four operations with no ``x-end-user-id`` when the credential is a ``ct-`` token."""
    # The local guard used to be unconditional, which blocked a documented and working use case:
    # the API's own description of this header says it is "Required with an API key, which carries
    # no user of its own. A client token already names its end user, so it may omit this header".
    # Verified live - a ct- token with food_logs:read and no header answers 200 - and this is the
    # device-side case client tokens exist for, where the device never learns an internal user id.
    respx_mock.post(FOOD_LOGS_URL).mock(return_value=httpx.Response(200, json=FOOD_LOG_PAYLOAD))
    respx_mock.get(FOOD_LOGS_URL).mock(return_value=httpx.Response(200, json=LIST_PAYLOAD))
    respx_mock.patch(LOG_URL).mock(return_value=httpx.Response(200, json=FOOD_LOG_PAYLOAD))
    respx_mock.delete(LOG_URL).mock(return_value=httpx.Response(200, json=DELETE_PAYLOAD))

    with make_client(api_key=CLIENT_TOKEN, default_end_user_id=None) as device:
        device.food_logs.create(FOODS)
        device.food_logs.list("2024-09-01", "2024-09-08")
        device.food_logs.update(LOG_ID, name="Lunch")
        device.food_logs.delete(LOG_ID)

    assert respx_mock.calls.call_count == 4
    for call in respx_mock.calls:
        assert "x-end-user-id" not in call.request.headers
        assert call.request.headers["authorization"] == f"Bearer {CLIENT_TOKEN}"


def test_a_client_token_may_also_pass_end_user_id_none_explicitly(
    make_client: Callable[..., January], respx_mock: respx.MockRouter
) -> None:
    """Treat an explicit ``None`` on a token as "the token names the user", not as an error."""
    route = respx_mock.get(FOOD_LOGS_URL).mock(return_value=httpx.Response(200, json=LIST_PAYLOAD))

    with make_client(api_key=CLIENT_TOKEN, default_end_user_id=END_USER_ID) as device:
        device.food_logs.list("2024-09-01", "2024-09-08", end_user_id=None)

    assert route.call_count == 1
    assert "x-end-user-id" not in route.calls.last.request.headers


def test_a_client_token_still_sends_an_end_user_it_was_given(
    make_client: Callable[..., January], respx_mock: respx.MockRouter
) -> None:
    """Forward an identifier the caller did supply, and let the API judge whether it matches."""
    # The exemption is about a missing header, not about suppressing one. A value that disagrees
    # with the token is refused upstream as ``end_user_mismatch``, which is the API's call to make.
    route = respx_mock.get(FOOD_LOGS_URL).mock(return_value=httpx.Response(200, json=LIST_PAYLOAD))

    with make_client(api_key=CLIENT_TOKEN, default_end_user_id=None) as device:
        device.food_logs.list("2024-09-01", "2024-09-08", end_user_id=END_USER_ID)

    assert route.calls.last.request.headers["x-end-user-id"] == END_USER_ID


@pytest.mark.parametrize(
    "api_key",
    [
        pytest.param("sk-test", id="account-key"),
        pytest.param("ct", id="prefixless-lookalike"),
        pytest.param("pk-unknown-kind", id="unrecognized-prefix"),
    ],
)
def test_anything_that_is_not_a_client_token_still_requires_an_end_user(
    make_client: Callable[..., January], respx_mock: respx.MockRouter, api_key: str
) -> None:
    """Keep the early local error for an API key, and for any prefix the SDK does not recognize."""
    # Guessing "client token" for an unknown shape would throw away the error that catches the
    # common mistake, so an unrecognized prefix is treated as an account key - the conservative
    # side of the fork. ``ct`` without the hyphen is not a client token.
    with (
        make_client(api_key=api_key, default_end_user_id=None) as anonymous,
        pytest.raises(ValueError, match="end_user_id is required"),
    ):
        anonymous.food_logs.create(FOODS)

    assert respx_mock.calls.call_count == 0


@pytest.mark.anyio
async def test_an_async_client_token_may_omit_the_end_user_too(
    make_async_client: Callable[..., AsyncJanuary], respx_mock: respx.MockRouter
) -> None:
    """Keep the two clients in step: the rule lives in the spec builder, not in either transport."""
    route = respx_mock.get(FOOD_LOGS_URL).mock(return_value=httpx.Response(200, json=LIST_PAYLOAD))

    async with make_async_client(api_key=CLIENT_TOKEN, default_end_user_id=None) as device:
        await device.food_logs.list("2024-09-01", "2024-09-08")

    assert route.call_count == 1
    assert "x-end-user-id" not in route.calls.last.request.headers


def test_the_client_default_end_user_is_used_when_a_call_omits_one(
    make_client: Callable[..., January], respx_mock: respx.MockRouter
) -> None:
    """Fall back to ``default_end_user_id`` so a single-user client need not repeat itself."""
    route = respx_mock.get(FOOD_LOGS_URL).mock(return_value=httpx.Response(200, json=LIST_PAYLOAD))

    with make_client(default_end_user_id=END_USER_ID) as scoped:
        scoped.food_logs.list("2024-09-01", "2024-09-08")

    assert route.calls.last.request.headers["x-end-user-id"] == END_USER_ID
