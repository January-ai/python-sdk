"""What actually goes out on the wire: headers, and the query string.

Four rules are pinned down here. The credential is always the client's own, no matter what the
caller put in ``default_headers``. The ``User-Agent`` identifies the SDK and its version, and is the
one header an integrator may replace, so their traffic can be told apart from everyone else's. The
end-user identifier resolves three ways - a per-call value, the client default, or an explicit
``None`` that suppresses the header entirely - and the difference between "omitted" and "explicitly
nothing" is exactly what the ``NOT_GIVEN`` sentinel exists to express. And a parameter the caller
did not supply is left out of the query string rather than sent as an empty value, so the server's
own default stays in force.

The last test is the one worth reading twice: a food-log call with no end user must fail before a
socket is opened, not after a round trip.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import httpx
import pytest
import respx

from january_ai import NOT_GIVEN, AsyncJanuary, January, NotGiven, __version__
from january_ai.types import FoodSelectionParam

from .conftest import ClientFactory

BASE_URL = "https://partners.january.ai"
CREDITS_URL = f"{BASE_URL}/v1.2/credits"
FOODS_URL = f"{BASE_URL}/v1.2/foods"
FOOD_LOGS_URL = f"{BASE_URL}/v1.2/food-logs"

END_USER_ID = "acme-user-8271"

CREDITS_PAYLOAD: dict[str, object] = {
    "plan": "free",
    "period_start": "2026-08-01",
    "period_end": "2026-08-31",
    "resets_at": "2026-09-01T00:00:00.000Z",
    "included_credits": 1000,
    "used_credits": 342,
    "remaining_credits": 658,
}

FOODS_LOGGED: Sequence[FoodSelectionParam] = [
    {"id": 101963552, "serving": {"id": 68051535, "quantity": 1.0}}
]

FOOD_SEARCH_PAYLOAD: dict[str, object] = {"total_count": 0, "items": []}
FOOD_LOG_LIST_PAYLOAD: dict[str, object] = {"total_count": 0, "items": []}
FOOD_LOG_PAYLOAD: dict[str, object] = {
    "id": "78129823-8ba2-4183-b13b-71f0e963c606",
    "foods": [],
    "timestamp_utc": "2024-09-13T11:34:56Z",
    "name": "Breakfast",
}


def credits_response() -> httpx.Response:
    """Build a successful credits response, the cheapest call that carries every SDK header."""
    return httpx.Response(200, json=CREDITS_PAYLOAD)


def last_request(route: respx.Route) -> httpx.Request:
    """Return the most recent request a route matched."""
    return route.calls.last.request


def test_authorization_carries_the_clients_api_key(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Send the API key as a bearer token on every request."""
    route = respx_mock.get(CREDITS_URL).mock(return_value=credits_response())

    client.credits.get()

    assert last_request(route).headers["authorization"] == "Bearer sk-test"


def test_user_agent_names_the_sdk_and_its_version(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Identify the SDK and its release in the User-Agent, ahead of the runtime details."""
    route = respx_mock.get(CREDITS_URL).mock(return_value=credits_response())

    client.credits.get()

    user_agent = last_request(route).headers["user-agent"]
    assert re.match(rf"^january-ai-python/{re.escape(__version__)}(\s|$)", user_agent)
    # The remainder identifies the interpreter and the HTTP library, which is what makes a
    # user-agent string worth reading in a server log at all.
    assert "python/" in user_agent
    assert "httpx/" in user_agent


def test_accept_header_asks_for_json(client: January, respx_mock: respx.MockRouter) -> None:
    """Negotiate JSON, so a proxy cannot talk the API into answering with anything else."""
    route = respx_mock.get(CREDITS_URL).mock(return_value=credits_response())

    client.credits.get()

    assert last_request(route).headers["accept"] == "application/json"


@pytest.mark.parametrize(
    ("default_end_user_id", "call_value", "expected"),
    [
        pytest.param(END_USER_ID, "per-call-user", "per-call-user", id="per-call-wins"),
        pytest.param(END_USER_ID, NOT_GIVEN, END_USER_ID, id="default-fills-in"),
        pytest.param(END_USER_ID, None, None, id="explicit-none-suppresses"),
        pytest.param(None, NOT_GIVEN, None, id="neither-set"),
        pytest.param(None, "per-call-user", "per-call-user", id="per-call-without-default"),
    ],
)
def test_end_user_id_resolution(
    make_client: ClientFactory,
    respx_mock: respx.MockRouter,
    default_end_user_id: str | None,
    call_value: str | NotGiven | None,
    expected: str | None,
) -> None:
    """Resolve x-end-user-id from the call, then the client default, then not at all."""
    # The three-way rule the whole SDK is built on. NOT_GIVEN means "I did not say", which the
    # client default answers; None means "no end user", which nothing may override. Collapsing the
    # two would make it impossible to make a call deliberately without an end user on a client that
    # has a default.
    client = make_client(default_end_user_id=default_end_user_id)
    route = respx_mock.get(CREDITS_URL).mock(return_value=credits_response())

    client.credits.get(end_user_id=call_value)

    assert last_request(route).headers.get("x-end-user-id") == expected


def test_explicit_none_suppresses_a_pinned_default_header(
    make_client: ClientFactory, respx_mock: respx.MockRouter
) -> None:
    """Drop x-end-user-id even when the caller pinned one in default_headers."""
    client = make_client(
        default_end_user_id=END_USER_ID,
        default_headers={"x-end-user-id": "pinned-user"},
    )
    route = respx_mock.get(CREDITS_URL).mock(return_value=credits_response())

    client.credits.get(end_user_id=None)

    assert "x-end-user-id" not in last_request(route).headers


def test_end_user_timezone_is_sent_only_when_given(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Forward the end user's timezone when the caller supplies one, and otherwise say nothing."""
    route = respx_mock.get(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(200, json=FOOD_LOG_LIST_PAYLOAD),
    )

    client.food_logs.list("2024-09-01", "2024-09-07", end_user_id=END_USER_ID)
    assert "x-end-user-timezone" not in last_request(route).headers

    client.food_logs.list(
        "2024-09-01",
        "2024-09-07",
        end_user_id=END_USER_ID,
        end_user_timezone="America/New_York",
    )
    assert last_request(route).headers["x-end-user-timezone"] == "America/New_York"


@pytest.mark.parametrize(
    ("end_user_id", "kind"),
    [
        ("josé@example.com", "non-ASCII"),
        ("用户1", "non-ASCII"),
        ("user\U0001f600", "non-ASCII"),
        ("user\r\nX-Injected: yes", "control"),
        ("user\x00", "control"),
    ],
    ids=["accented-email", "cjk", "emoji", "crlf", "nul"],
)
def test_non_ascii_end_user_id_is_refused_by_name(
    client: January, respx_mock: respx.MockRouter, end_user_id: str, kind: str
) -> None:
    """Say which argument is wrong and why, instead of leaking httpx's codec error.

    An email address with an accent and a non-Latin username are ordinary inputs, and httpx
    answered both with ``'ascii' codec can't encode character '\\xe9' in position 3`` - a message
    that names neither the SDK, the parameter, nor the constraint. Nothing must reach the wire.
    """
    route = respx_mock.get(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(200, json=FOOD_LOG_LIST_PAYLOAD),
    )

    with pytest.raises(ValueError) as caught:
        client.food_logs.list("2024-09-01", "2024-09-07", end_user_id=end_user_id)

    message = str(caught.value)
    assert "end_user_id" in message
    assert "x-end-user-id" in message
    assert f"{kind} character" in message
    assert route.call_count == 0
    assert len(respx_mock.calls) == 0


def test_non_ascii_end_user_timezone_is_refused_by_name(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Apply the same check to the other value the SDK turns into a header."""
    route = respx_mock.get(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(200, json=FOOD_LOG_LIST_PAYLOAD),
    )

    with pytest.raises(ValueError) as caught:
        client.food_logs.list(
            "2024-09-01",
            "2024-09-07",
            end_user_id=END_USER_ID,
            end_user_timezone="\U0001f550",
        )

    message = str(caught.value)
    assert "end_user_timezone" in message
    assert "x-end-user-timezone" in message
    assert route.call_count == 0


def test_a_non_ascii_default_end_user_id_is_refused_the_same_way(
    make_client: ClientFactory, respx_mock: respx.MockRouter
) -> None:
    """The check follows the resolved value, so a client default is caught as readily as a call."""
    client = make_client(default_end_user_id="用户1")
    route = respx_mock.get(CREDITS_URL).mock(return_value=credits_response())

    with pytest.raises(ValueError, match="end_user_id must be printable ASCII"):
        client.credits.get()

    assert route.call_count == 0


def test_an_ascii_end_user_id_with_punctuation_is_still_accepted(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """The check bounds nothing an ordinary identifier does: an email or a UUID sails through."""
    route = respx_mock.get(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(200, json=FOOD_LOG_LIST_PAYLOAD),
    )

    client.food_logs.list("2024-09-01", "2024-09-07", end_user_id="user+tag@example.com")

    assert last_request(route).headers["x-end-user-id"] == "user+tag@example.com"


@pytest.mark.anyio
async def test_async_non_ascii_end_user_id_is_refused_by_name(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """The asynchronous client builds the same headers and must refuse the same values."""
    route = respx_mock.get(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(200, json=FOOD_LOG_LIST_PAYLOAD),
    )

    with pytest.raises(ValueError, match="end_user_id must be printable ASCII"):
        await async_client.food_logs.list(
            "2024-09-01", "2024-09-07", end_user_id="josé@example.com"
        )

    assert route.call_count == 0


def test_default_headers_are_sent_but_cannot_clobber_authorization(
    make_client: ClientFactory, respx_mock: respx.MockRouter
) -> None:
    """Merge the caller's headers in, while keeping the credential the client was built with."""
    client = make_client(
        default_headers={
            "X-Tenant": "acme",
            "Authorization": "Bearer sk-somebody-elses-key",
            "Accept": "text/html",
        },
    )
    route = respx_mock.get(CREDITS_URL).mock(return_value=credits_response())

    client.credits.get()

    request = last_request(route)
    assert request.headers["x-tenant"] == "acme"
    # Authentication and content negotiation are the SDK's own business: a header set here by
    # mistake must not be able to send the wrong credential or ask for a format the SDK cannot
    # parse.
    assert request.headers["authorization"] == "Bearer sk-test"
    assert request.headers["accept"] == "application/json"
    assert request.headers.get_list("authorization") == ["Bearer sk-test"]


def test_default_headers_cannot_clobber_the_content_type_of_a_json_body(
    make_client: ClientFactory, respx_mock: respx.MockRouter
) -> None:
    """Describe a JSON body as JSON, whatever the caller pinned in ``default_headers``."""
    # Content-Type used to be left to httpx, which sets it only where nothing has claimed the name
    # already - so default_headers={"Content-Type": "text/plain"} shipped a JSON body advertised as
    # text. The body never changed; only the label did, which is the worst of both. The API happens
    # to tolerate it today, but any proxy or stricter deployment in the path is entitled to reject
    # it, and the caller gets a protocol violation they did not ask for and cannot see.
    client = make_client(default_headers={"Content-Type": "text/plain", "X-Tenant": "acme"})
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(201, json=FOOD_LOG_PAYLOAD),
    )

    client.food_logs.create(FOODS_LOGGED, end_user_id=END_USER_ID)

    request = last_request(route)
    assert request.headers["content-type"] == "application/json"
    assert request.headers.get_list("content-type") == ["application/json"]
    assert (
        request.content == b'{"foods":[{"id":101963552,"serving":{"id":68051535,"quantity":1.0}}]}'
    )
    # The headers the SDK does not claim are still the caller's to set.
    assert request.headers["x-tenant"] == "acme"


def test_a_request_with_no_body_advertises_no_content_type(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Leave Content-Type off a GET, which has no content to type."""
    route = respx_mock.get(CREDITS_URL).mock(return_value=credits_response())

    client.credits.get()

    assert "content-type" not in last_request(route).headers


def test_default_headers_may_override_the_user_agent(
    make_client: ClientFactory, respx_mock: respx.MockRouter
) -> None:
    """Let an integrator brand their traffic by replacing the User-Agent, and only that."""
    client = make_client(default_headers={"User-Agent": "acme-nutrition-app/2.1"})
    route = respx_mock.get(CREDITS_URL).mock(return_value=credits_response())

    client.credits.get()

    request = last_request(route)
    assert request.headers["user-agent"] == "acme-nutrition-app/2.1"
    # Replaced, not appended: two User-Agent headers would be a protocol violation.
    assert request.headers.get_list("user-agent") == ["acme-nutrition-app/2.1"]
    assert request.headers["authorization"] == "Bearer sk-test"


def test_query_parameters_drop_none_values(client: January, respx_mock: respx.MockRouter) -> None:
    """Leave an unsupplied parameter out of the query string entirely."""
    route = respx_mock.get(FOODS_URL).mock(
        return_value=httpx.Response(200, json=FOOD_SEARCH_PAYLOAD),
    )

    client.foods.search("greek yogurt", category=None, limit=None)

    # An empty category= would override the server's own default with nothing at all, which is not
    # what "I did not pass one" means.
    assert dict(last_request(route).url.params) == {"query": "greek yogurt"}


def test_query_parameters_that_are_given_are_sent(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Send every parameter the caller did supply, rendered as the API expects."""
    route = respx_mock.get(FOODS_URL).mock(
        return_value=httpx.Response(200, json=FOOD_SEARCH_PAYLOAD),
    )

    client.foods.search("greek yogurt", category="branded", limit=5)

    assert dict(last_request(route).url.params) == {
        "query": "greek yogurt",
        "category": "branded",
        "limit": "5",
    }


def test_food_log_without_an_end_user_raises_before_any_request(
    make_client: ClientFactory,
    respx_mock: respx.MockRouter,
    recorded_sleeps: list[float],
) -> None:
    """Refuse a food-log call with no end user locally, without opening a connection."""
    # The endpoint requires x-end-user-id, so a client with no default and a call that names no
    # user cannot possibly succeed. Finding that out from a round trip would be slower, would spend
    # a request against the caller's rate limit, and would surface as a 400 whose wording the
    # caller then has to interpret; a ValueError raised at the call site says it immediately.
    client = make_client(default_end_user_id=None)
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(201, json=FOOD_LOG_PAYLOAD),
    )

    with pytest.raises(ValueError, match="end_user_id is required"):
        client.food_logs.create([{"id": 101963552, "serving": {"id": 68051535, "quantity": 1.0}}])

    # The route exists and would have answered; the point is that nothing ever reached it.
    assert route.call_count == 0
    assert not route.called
    assert len(respx_mock.calls) == 0
    assert recorded_sleeps == []


def test_food_log_with_an_explicit_none_end_user_raises_before_any_request(
    make_client: ClientFactory, respx_mock: respx.MockRouter
) -> None:
    """Refuse a deliberate "no end user" on an endpoint that has no meaning without one."""
    client = make_client(default_end_user_id=END_USER_ID)
    route = respx_mock.get(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(200, json=FOOD_LOG_LIST_PAYLOAD),
    )

    with pytest.raises(ValueError, match="end_user_id is required"):
        client.food_logs.list("2024-09-01", "2024-09-07", end_user_id=None)

    assert route.call_count == 0
    assert len(respx_mock.calls) == 0
