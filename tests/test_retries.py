"""Retry behaviour: what gets replayed, how many times, and how long the client waits between.

Three separate decisions are exercised here and it is worth keeping them apart while reading.

*Whether* to retry an error response is decided by the error ``code`` first and the HTTP status
second: a known code is authoritative even when its status disagrees, and only an unknown or absent
code falls through to the status class. *Whether* to retry a transport failure is decided by how far
the request got: a connection that was never established can always be replayed, while a failure
that struck after the bytes went out may only be replayed for an operation that says it is safe -
which creating a food log emphatically does not. *How long* to wait is the server's ``Retry-After``
when it sent one and jittered exponential backoff otherwise.

No test here sleeps: the client's sleeper is replaced by one that records the delay it was asked
for, so the exact backoff is asserted rather than endured. Every assertion is made against both the
synchronous and the asynchronous client, and the asynchronous ones run on asyncio and on trio.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
import respx

from january_ai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncJanuary,
    BadRequestError,
    CreditLimitExceededError,
    InternalServerError,
    January,
    RateLimitError,
)
from january_ai._constants import (
    DEFAULT_MAX_RETRIES,
    INITIAL_RETRY_DELAY,
    MAX_HONORED_RETRY_AFTER,
    MAX_RETRY_DELAY,
    MAX_TOTAL_RETRY_AFTER_WAIT,
)
from january_ai.types import FoodSelectionParam

from .conftest import AsyncClientFactory, ClientFactory

BASE_URL = "https://partners.january.ai"
FOOD_ID = 101963552
FOOD_URL = f"{BASE_URL}/v1.2/foods/{FOOD_ID}"
FOOD_LOGS_URL = f"{BASE_URL}/v1.2/food-logs"

END_USER_ID = "acme-user-8271"
DOCS_URL = "https://docs.january.ai/rest-api/api-overview"

ATTEMPTS = 1 + DEFAULT_MAX_RETRIES
"""Total requests a fully exhausted retry budget produces: the first attempt plus the retries."""

FOOD_PAYLOAD: dict[str, object] = {
    "id": FOOD_ID,
    "name": "Dipped Banana Bites",
    "nutrients": {"calories": {"value": 300, "unit": "kcal"}},
    "servings": [
        {
            "id": 68051535,
            "quantity": 1,
            "unit": "oz",
            "scaling_factor": 1,
            "weight_grams": 28,
            "is_primary": True,
        }
    ],
}

FOOD_LOG_PAYLOAD: dict[str, object] = {
    "id": "78129823-8ba2-4183-b13b-71f0e963c606",
    "foods": [],
    "timestamp_utc": "2024-09-13T11:34:56Z",
    "name": "Breakfast",
}

FOODS_LOGGED: Sequence[FoodSelectionParam] = [
    {"id": FOOD_ID, "serving": {"id": 68051535, "quantity": 1.0}}
]

# The five codes the API documents as safe to retry with backoff, each on the status it arrives on.
RETRYABLE_CASES: list[tuple[int, str]] = [
    (429, "rate_limited"),
    (500, "internal_error"),
    (502, "upstream_error"),
    (503, "service_unavailable"),
    (504, "upstream_timeout"),
]

# The retryable server failures, without the 429. Each of these means a request reached a server:
# a gateway answering upstream_timeout is saying it forwarded the request to an origin whose fate
# it does not know. That is the same duplicate-write hazard as a ReadTimeout, so an operation that
# refuses ambiguous replays must refuse these too. The 429 is excluded deliberately - see the
# carve-out test below.
AMBIGUOUS_STATUS_CASES: list[tuple[int, str]] = [
    (500, "internal_error"),
    (502, "upstream_error"),
    (503, "service_unavailable"),
    (504, "upstream_timeout"),
]

# Codes that must never be replayed. The last two are the interesting ones: both sit on a status
# that would otherwise be retried, and the code has to win.
NON_RETRYABLE_CASES: list[tuple[int, str]] = [
    (400, "invalid_request"),
    (401, "unauthorized"),
    (403, "forbidden"),
    (404, "not_found"),
    (413, "payload_too_large"),
    (501, "not_implemented"),
    (429, "credit_limit_exceeded"),
    (503, "not_implemented"),
]


def error_body(code: str, *, message: str | None = None) -> dict[str, str]:
    """Build the error envelope the API documents: a message, a code, and a docs link."""
    return {
        "message": message if message is not None else f"The request failed: {code}.",
        "code": code,
        "docs_url": DOCS_URL,
    }


def food_response() -> httpx.Response:
    """Build the successful food lookup used to end a sequence of failures."""
    return httpx.Response(200, json=FOOD_PAYLOAD)


@pytest.fixture
def user_client(make_client: ClientFactory) -> January:
    """A client with a default end user, so food-log calls need no identifier of their own."""
    return make_client(default_end_user_id=END_USER_ID)


@pytest.fixture
async def user_async_client(make_async_client: AsyncClientFactory) -> AsyncJanuary:
    """The asynchronous counterpart of :func:`user_client`."""
    return make_async_client(default_end_user_id=END_USER_ID)


@pytest.mark.parametrize(
    ("status", "code"), RETRYABLE_CASES, ids=[code for _, code in RETRYABLE_CASES]
)
def test_retryable_code_is_replayed_and_then_succeeds(
    client: January, respx_mock: respx.MockRouter, status: int, code: str
) -> None:
    """Replay each documented retryable failure and return the result of the second attempt."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[httpx.Response(status, json=error_body(code)), food_response()],
    )

    food = client.foods.get(FOOD_ID)

    assert food.id == FOOD_ID
    assert route.call_count == 2


@pytest.mark.parametrize(
    ("status", "code"),
    NON_RETRYABLE_CASES,
    ids=[f"{status}-{code}" for status, code in NON_RETRYABLE_CASES],
)
def test_non_retryable_code_is_sent_exactly_once(
    client: January,
    respx_mock: respx.MockRouter,
    recorded_sleeps: list[float],
    status: int,
    code: str,
) -> None:
    """Give up immediately on a failure that repeating cannot fix."""
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(status, json=error_body(code)),
    )

    with pytest.raises(APIStatusError) as excinfo:
        client.foods.get(FOOD_ID)

    assert route.call_count == 1
    assert recorded_sleeps == []
    assert excinfo.value.code == code


def test_credit_limit_exceeded_beats_its_retryable_status(
    client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Never replay an exhausted credit allowance, even though it arrives as a 429."""
    # The one place where the code has to override the status outright. A 429 is retryable by
    # status, but this particular 429 means the account is out of credits until the next calendar
    # month, so every replay would spend the caller's retry budget on a certain failure.
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(429, json=error_body("credit_limit_exceeded")),
    )

    with pytest.raises(CreditLimitExceededError):
        client.foods.get(FOOD_ID)

    assert route.call_count == 1
    assert recorded_sleeps == []


def test_429_without_a_code_falls_back_to_the_status(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Retry a 429 whose body carries no code, since the status alone says the request may work."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[httpx.Response(429, json={"message": "Too many requests."}), food_response()],
    )

    assert client.foods.get(FOOD_ID).id == FOOD_ID
    assert route.call_count == 2


def test_unknown_code_on_a_400_is_not_retried(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Treat a code added after this release by its status class, and a 400 is final."""
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(400, json=error_body("some_future_code")),
    )

    with pytest.raises(BadRequestError):
        client.foods.get(FOOD_ID)

    assert route.call_count == 1


def test_max_retries_zero_sends_exactly_one_request(
    make_client: ClientFactory,
    respx_mock: respx.MockRouter,
    recorded_sleeps: list[float],
) -> None:
    """Disable retrying entirely when the caller asks for a budget of zero."""
    client = make_client(max_retries=0)
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(500, json=error_body("internal_error")),
    )

    with pytest.raises(InternalServerError):
        client.foods.get(FOOD_ID)

    assert route.call_count == 1
    assert recorded_sleeps == []


def test_exhausting_the_budget_raises_the_last_error(
    client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Raise the failure from the final attempt, not the one that started the sequence."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[
            httpx.Response(500, json=error_body("internal_error", message="first")),
            httpx.Response(502, json=error_body("upstream_error", message="second")),
            httpx.Response(503, json=error_body("service_unavailable", message="third")),
        ],
    )

    with pytest.raises(InternalServerError) as excinfo:
        client.foods.get(FOOD_ID)

    assert route.call_count == ATTEMPTS
    assert len(recorded_sleeps) == DEFAULT_MAX_RETRIES
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "service_unavailable"
    assert excinfo.value.message == "third"


def test_retry_after_seconds_is_honoured_verbatim(
    client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Wait exactly as long as the server asked, rather than applying the SDK's own backoff."""
    respx_mock.get(FOOD_URL).mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limited"), headers={"retry-after": "3"}),
            food_response(),
        ],
    )

    assert client.foods.get(FOOD_ID).id == FOOD_ID
    # Not jittered, not rounded: the server knows when its window rolls over.
    assert recorded_sleeps == [3.0]


def test_retry_after_http_date_is_honoured(
    client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Accept the other form the header allows, an absolute HTTP-date, and wait until then."""
    resume_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    respx_mock.get(FOOD_URL).mock(
        side_effect=[
            httpx.Response(
                429,
                json=error_body("rate_limited"),
                headers={"retry-after": format_datetime(resume_at, usegmt=True)},
            ),
            food_response(),
        ],
    )

    assert client.foods.get(FOOD_ID).id == FOOD_ID
    assert len(recorded_sleeps) == 1
    # An HTTP-date has one-second resolution, so the computed wait is at most the full 30 seconds
    # and a little less once the header's sub-second remainder is truncated away.
    assert 25.0 <= recorded_sleeps[0] <= 30.0


def test_negative_retry_after_clamps_to_zero(
    client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Retry immediately rather than computing a negative delay from a stale header."""
    respx_mock.get(FOOD_URL).mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limited"), headers={"retry-after": "-5"}),
            food_response(),
        ],
    )

    assert client.foods.get(FOOD_ID).id == FOOD_ID
    assert recorded_sleeps == [0.0]


def test_retry_after_beyond_the_cap_raises_immediately(
    client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Hand a multi-minute wait back to the caller instead of blocking inside a single call."""
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(
            429, json=error_body("rate_limited"), headers={"retry-after": "300"}
        ),
    )

    with pytest.raises(RateLimitError) as excinfo:
        client.foods.get(FOOD_ID)

    assert route.call_count == 1
    assert recorded_sleeps == []
    # The wait is still reported, so the caller can schedule the retry themselves.
    assert excinfo.value.retry_after == 300.0
    assert excinfo.value.retry_after > MAX_HONORED_RETRY_AFTER


def test_retry_after_beyond_the_cap_says_why_it_did_not_wait(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Explain the refusal to sleep, which the API's own message cannot possibly mention.

    Without this the caller sees only "Too many requests (status 429)" and cannot tell a client
    that gave up retrying from one that decided the wait was too long to absorb.
    """
    respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(
            429, json=error_body("rate_limited"), headers={"retry-after": "300"}
        ),
    )

    with pytest.raises(RateLimitError) as excinfo:
        client.foods.get(FOOD_ID)

    rendered = str(excinfo.value)
    assert "300s" in rendered
    assert f"{MAX_HONORED_RETRY_AFTER:g}s" in rendered
    assert "did not wait" in rendered
    # The API's own message is left exactly as it arrived; the explanation is added around it.
    assert excinfo.value.message == "The request failed: rate_limited."


def test_retry_after_is_exposed_on_every_status_not_only_429(
    make_client: ClientFactory, respx_mock: respx.MockRouter
) -> None:
    """A 503 that says to back off for ten minutes must say so through the error, not the headers.

    ``retry_after`` used to live on ``RateLimitError`` alone, so this value was reachable only by
    digging into ``e.response.headers`` - which nothing in the SDK told the caller to do.
    """
    client = make_client(max_retries=0)
    respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(
            503, json=error_body("service_unavailable"), headers={"retry-after": "600"}
        ),
    )

    with pytest.raises(InternalServerError) as excinfo:
        client.foods.get(FOOD_ID)

    assert excinfo.value.retry_after == 600.0


def test_a_retryable_503_over_the_cap_is_explained_too(
    client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """The explanation is about the wait, not about the status, so it applies beyond 429."""
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(
            503, json=error_body("service_unavailable"), headers={"retry-after": "600"}
        ),
    )

    with pytest.raises(InternalServerError) as excinfo:
        client.foods.get(FOOD_ID)

    assert route.call_count == 1
    assert recorded_sleeps == []
    assert excinfo.value.retry_after == 600.0
    assert "600s" in str(excinfo.value)


def test_retry_after_waits_are_capped_in_total_across_one_call(
    make_client: ClientFactory, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Bound the sum of the server-directed waits, not merely each one on its own.

    The per-wait cap alone let a server repeating ``Retry-After: 60`` hold a single method call for
    ``max_retries x 60`` seconds - five minutes at ``max_retries=5``. ``timeout=`` does not bound
    that, since it covers one attempt.
    """
    client = make_client(max_retries=5)
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(
            429, json=error_body("rate_limited"), headers={"retry-after": "60"}
        ),
    )

    with pytest.raises(RateLimitError) as excinfo:
        client.foods.get(FOOD_ID)

    assert recorded_sleeps == [60.0]
    assert sum(recorded_sleeps) <= MAX_TOTAL_RETRY_AFTER_WAIT
    assert route.call_count == 2
    rendered = str(excinfo.value)
    assert "60s" in rendered
    assert f"{MAX_TOTAL_RETRY_AFTER_WAIT:g}s total" in rendered


def test_short_retry_after_waits_are_all_honoured_within_the_budget(
    make_client: ClientFactory, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """A server asking for a few seconds at a time keeps its whole retry budget."""
    client = make_client(max_retries=5)
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(
            429, json=error_body("rate_limited"), headers={"retry-after": "2"}
        ),
    )

    with pytest.raises(RateLimitError):
        client.foods.get(FOOD_ID)

    assert recorded_sleeps == [2.0] * 5
    assert route.call_count == 6


def test_the_budget_does_not_truncate_the_sdks_own_backoff(
    make_client: ClientFactory, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Only server-directed waits are charged: cutting the SDK's own backoff would help nobody.

    Five jittered waits at the 8s ceiling sum to more than the 60s Retry-After budget in the worst
    case, and truncating them would cut short exactly the retries that make a transient 5xx
    survivable.
    """
    client = make_client(max_retries=5)
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(500, json=error_body("internal_error")),
    )

    with pytest.raises(InternalServerError):
        client.foods.get(FOOD_ID)

    assert route.call_count == 6
    assert len(recorded_sleeps) == 5


def test_a_retry_after_followed_by_backoff_charges_only_the_first(
    make_client: ClientFactory, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """A response with no ``Retry-After`` spends the SDK's backoff and none of the budget."""
    client = make_client(max_retries=3)
    respx_mock.get(FOOD_URL).mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limited"), headers={"retry-after": "55"}),
            httpx.Response(500, json=error_body("internal_error")),
            httpx.Response(429, json=error_body("rate_limited"), headers={"retry-after": "55"}),
            food_response(),
        ],
    )

    with pytest.raises(RateLimitError) as excinfo:
        client.foods.get(FOOD_ID)

    # 55 + 55 would exceed the budget, so the third response is raised rather than slept through -
    # even though the backoff wait in between spent no budget at all.
    assert len(recorded_sleeps) == 2
    assert recorded_sleeps[0] == 55.0
    assert recorded_sleeps[1] < MAX_RETRY_DELAY
    assert "already waited 55s" in str(excinfo.value)


def test_backoff_without_retry_after_stays_inside_the_jitter_band(
    client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Grow the delay exponentially and scale it by a jitter factor in [0.75, 1.0)."""
    respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(500, json=error_body("internal_error")),
    )

    with pytest.raises(InternalServerError):
        client.foods.get(FOOD_ID)

    assert len(recorded_sleeps) == DEFAULT_MAX_RETRIES
    for attempt, slept in enumerate(recorded_sleeps):
        ceiling = min(INITIAL_RETRY_DELAY * 2.0**attempt, MAX_RETRY_DELAY)
        # The upper bound is exclusive: the jitter factor is drawn from [0.75, 1.0), so a delay
        # equal to the ceiling would mean the jitter was never applied.
        assert 0.75 * ceiling <= slept < ceiling
    # The bands do not overlap, so growth is observable without depending on the seeded draws.
    assert recorded_sleeps[1] > recorded_sleeps[0]


def test_connect_error_is_retried_and_then_raises_a_connection_error(
    client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Replay a connection that was never established, then report the failure as its own type."""
    route = respx_mock.get(FOOD_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    with pytest.raises(APIConnectionError) as excinfo:
        client.foods.get(FOOD_ID)

    # Exactly APIConnectionError, not the APITimeoutError subclass: nothing timed out here.
    assert type(excinfo.value) is APIConnectionError
    assert route.call_count == ATTEMPTS
    assert len(recorded_sleeps) == DEFAULT_MAX_RETRIES
    assert excinfo.value.request is not None


def test_connect_error_that_clears_is_retried_into_a_success(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Return the successful attempt when a refused connection succeeds on the replay."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[httpx.ConnectError("connection refused"), food_response()],
    )

    assert client.foods.get(FOOD_ID).id == FOOD_ID
    assert route.call_count == 2


def test_connect_timeout_is_retried_and_then_raises_a_timeout_error(
    client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Surface a timeout as APITimeoutError, after spending the whole retry budget on it."""
    route = respx_mock.get(FOOD_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

    with pytest.raises(APITimeoutError):
        client.foods.get(FOOD_ID)

    assert route.call_count == ATTEMPTS
    assert len(recorded_sleeps) == DEFAULT_MAX_RETRIES


def test_read_timeout_is_retried_on_an_idempotent_read(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Replay an ambiguous failure on a read, where repeating the request changes nothing."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[httpx.ReadTimeout("timed out waiting for the response"), food_response()],
    )

    assert client.foods.get(FOOD_ID).id == FOOD_ID
    assert route.call_count == 2


def test_read_timeout_is_never_retried_on_a_food_log_create(
    user_client: January, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Refuse to replay an ambiguous failure on the one operation that is not idempotent."""
    # The most important rule in this file, and the counterpart of the test directly above. A
    # ReadTimeout is *ambiguous*: the request bytes were already on the wire, so the server may
    # well have created the food log and only the reply was lost. Creating a log is not idempotent
    # and the API accepts no idempotency key, so replaying it would silently log the end user's
    # meal a second time - corruption the caller has no way to detect and the end user sees as
    # double calories. The create spec therefore carries retry_ambiguous=False, and that flag must
    # beat the client's retry budget: one attempt, no backoff, the timeout raised straight to the
    # caller so they can list the day and decide for themselves.
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        side_effect=httpx.ReadTimeout("timed out waiting for the response"),
    )

    with pytest.raises(APITimeoutError):
        user_client.food_logs.create(FOODS_LOGGED)

    assert route.call_count == 1
    assert recorded_sleeps == []


def test_connect_error_is_still_retried_on_a_food_log_create(
    user_client: January, respx_mock: respx.MockRouter
) -> None:
    """Keep replaying failures that never reached the server, even on a create."""
    # retry_ambiguous=False suppresses only the ambiguous class of failure, not retrying as such.
    # A connection that was never established cannot have created anything, so replaying it is
    # free of the duplicate-log risk that governs the test above.
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        side_effect=[
            httpx.ConnectError("connection refused"),
            httpx.Response(201, json=FOOD_LOG_PAYLOAD),
        ],
    )

    log = user_client.food_logs.create(FOODS_LOGGED)

    assert log.id == FOOD_LOG_PAYLOAD["id"]
    assert route.call_count == 2


@pytest.mark.parametrize(
    ("status", "code"), AMBIGUOUS_STATUS_CASES, ids=[code for _, code in AMBIGUOUS_STATUS_CASES]
)
def test_retryable_server_error_is_never_retried_on_a_food_log_create(
    user_client: January,
    respx_mock: respx.MockRouter,
    recorded_sleeps: list[float],
    status: int,
    code: str,
) -> None:
    """Treat a retryable 5xx on a create as the ambiguous failure it is, and refuse to replay it."""
    # The same rule as the ReadTimeout test above, on the failure channel that actually dominates
    # in production. A 504 upstream_timeout *is* a timed-out create, reported by the API's own
    # gateway rather than observed from this end of the wire; the origin behind it may already have
    # written the log. Replaying would log the meal twice, which the caller cannot detect and the
    # end user sees as double calories. retry_ambiguous=False has to govern both channels, not just
    # the one the SDK happens to see as an exception.
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(status, json=error_body(code)),
    )

    with pytest.raises(InternalServerError):
        user_client.food_logs.create(FOODS_LOGGED)

    assert route.call_count == 1
    assert recorded_sleeps == []


def test_rate_limit_is_still_retried_on_a_food_log_create(
    user_client: January, respx_mock: respx.MockRouter
) -> None:
    """Keep replaying a 429 on a create: a refused request cannot have written anything."""
    # The carve-out that keeps the rule above from being over-broad. Rate limiting is enforced
    # ahead of the handler, so a 429 is proof the create did *not* happen - there is no duplicate
    # to fear and no reason to spend the caller's retry budget on a guess.
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limited")),
            httpx.Response(201, json=FOOD_LOG_PAYLOAD),
        ],
    )

    log = user_client.food_logs.create(FOODS_LOGGED)

    assert log.id == FOOD_LOG_PAYLOAD["id"]
    assert route.call_count == 2


def test_retryable_server_error_is_still_retried_on_an_idempotent_read(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Leave every other operation retrying server errors exactly as before."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[httpx.Response(504, json=error_body("upstream_timeout")), food_response()],
    )

    assert client.foods.get(FOOD_ID).id == FOOD_ID
    assert route.call_count == 2


def test_proxy_error_is_replayed_like_any_other_connection_failure(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Classify a rejected proxy tunnel as pre-send, since the origin never saw the request."""
    # httpx raises ProxyError straight off TransportError rather than off NetworkError, so it used
    # to miss every row of the classification table and fall through to "fatal" - silently
    # disabling retrying altogether for anyone behind a proxy, which merely having HTTPS_PROXY set
    # in the environment is enough to arrange. The proxy raises while establishing the tunnel,
    # before the caller's request is forwarded, so replaying is as safe as a ConnectError.
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[httpx.ProxyError("502 Bad Gateway from the proxy"), food_response()],
    )

    assert client.foods.get(FOOD_ID).id == FOOD_ID
    assert route.call_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code"), RETRYABLE_CASES, ids=[code for _, code in RETRYABLE_CASES]
)
async def test_async_retryable_code_is_replayed_and_then_succeeds(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter, status: int, code: str
) -> None:
    """Replay each documented retryable failure on the asynchronous client too."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[httpx.Response(status, json=error_body(code)), food_response()],
    )

    food = await async_client.foods.get(FOOD_ID)

    assert food.id == FOOD_ID
    assert route.call_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code"),
    NON_RETRYABLE_CASES,
    ids=[f"{status}-{code}" for status, code in NON_RETRYABLE_CASES],
)
async def test_async_non_retryable_code_is_sent_exactly_once(
    async_client: AsyncJanuary,
    respx_mock: respx.MockRouter,
    recorded_sleeps: list[float],
    status: int,
    code: str,
) -> None:
    """Give up immediately on the asynchronous client as well."""
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(status, json=error_body(code)),
    )

    with pytest.raises(APIStatusError) as excinfo:
        await async_client.foods.get(FOOD_ID)

    assert route.call_count == 1
    assert recorded_sleeps == []
    assert excinfo.value.code == code


@pytest.mark.anyio
async def test_async_max_retries_zero_sends_exactly_one_request(
    make_async_client: AsyncClientFactory,
    respx_mock: respx.MockRouter,
    recorded_sleeps: list[float],
) -> None:
    """Honour a retry budget of zero on the asynchronous client too."""
    client = make_async_client(max_retries=0)
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(500, json=error_body("internal_error")),
    )

    with pytest.raises(InternalServerError):
        await client.foods.get(FOOD_ID)

    assert route.call_count == 1
    assert recorded_sleeps == []


@pytest.mark.anyio
async def test_async_exhausting_the_budget_raises_the_last_error(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Raise the failure from the final attempt, awaiting the same number of backoffs."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[
            httpx.Response(500, json=error_body("internal_error", message="first")),
            httpx.Response(502, json=error_body("upstream_error", message="second")),
            httpx.Response(503, json=error_body("service_unavailable", message="third")),
        ],
    )

    with pytest.raises(InternalServerError) as excinfo:
        await async_client.foods.get(FOOD_ID)

    assert route.call_count == ATTEMPTS
    assert len(recorded_sleeps) == DEFAULT_MAX_RETRIES
    assert excinfo.value.status_code == 503
    assert excinfo.value.message == "third"


@pytest.mark.anyio
async def test_async_429_without_a_code_falls_back_to_the_status(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Fall back to the status class on the asynchronous client as well."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[httpx.Response(429, json={"message": "Too many requests."}), food_response()],
    )

    food = await async_client.foods.get(FOOD_ID)

    assert food.id == FOOD_ID
    assert route.call_count == 2


@pytest.mark.anyio
async def test_async_retry_after_seconds_is_honoured_verbatim(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Await exactly the wait the server asked for."""
    respx_mock.get(FOOD_URL).mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limited"), headers={"retry-after": "3"}),
            food_response(),
        ],
    )

    food = await async_client.foods.get(FOOD_ID)

    assert food.id == FOOD_ID
    assert recorded_sleeps == [3.0]


@pytest.mark.anyio
async def test_async_retry_after_beyond_the_cap_raises_immediately(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Hand a multi-minute wait back to the caller rather than parking a coroutine on it."""
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(
            429, json=error_body("rate_limited"), headers={"retry-after": "300"}
        ),
    )

    with pytest.raises(RateLimitError) as excinfo:
        await async_client.foods.get(FOOD_ID)

    assert route.call_count == 1
    assert recorded_sleeps == []
    assert excinfo.value.retry_after == 300.0


@pytest.mark.anyio
async def test_async_retry_after_waits_are_capped_in_total_across_one_call(
    make_async_client: AsyncClientFactory,
    respx_mock: respx.MockRouter,
    recorded_sleeps: list[float],
) -> None:
    """The asynchronous client keeps the same total budget, so neither can park a caller longer."""
    client = make_async_client(max_retries=5)
    route = respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(
            429, json=error_body("rate_limited"), headers={"retry-after": "60"}
        ),
    )

    with pytest.raises(RateLimitError) as excinfo:
        await client.foods.get(FOOD_ID)

    assert recorded_sleeps == [60.0]
    assert route.call_count == 2
    assert f"{MAX_TOTAL_RETRY_AFTER_WAIT:g}s total" in str(excinfo.value)


@pytest.mark.anyio
async def test_async_retry_after_beyond_the_cap_is_explained(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """And the same explanation is attached, so the two clients read identically."""
    respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(
            429, json=error_body("rate_limited"), headers={"retry-after": "300"}
        ),
    )

    with pytest.raises(RateLimitError) as excinfo:
        await async_client.foods.get(FOOD_ID)

    assert "did not wait" in str(excinfo.value)


@pytest.mark.anyio
async def test_async_backoff_stays_inside_the_jitter_band(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Use the same exponential backoff envelope as the synchronous client."""
    respx_mock.get(FOOD_URL).mock(
        return_value=httpx.Response(500, json=error_body("internal_error")),
    )

    with pytest.raises(InternalServerError):
        await async_client.foods.get(FOOD_ID)

    assert len(recorded_sleeps) == DEFAULT_MAX_RETRIES
    for attempt, slept in enumerate(recorded_sleeps):
        ceiling = min(INITIAL_RETRY_DELAY * 2.0**attempt, MAX_RETRY_DELAY)
        assert 0.75 * ceiling <= slept < ceiling


@pytest.mark.anyio
async def test_async_connect_error_is_retried_and_then_raises_a_connection_error(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Replay a connection that was never established, then report it as APIConnectionError."""
    route = respx_mock.get(FOOD_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    with pytest.raises(APIConnectionError) as excinfo:
        await async_client.foods.get(FOOD_ID)

    assert type(excinfo.value) is APIConnectionError
    assert route.call_count == ATTEMPTS
    assert len(recorded_sleeps) == DEFAULT_MAX_RETRIES


@pytest.mark.anyio
async def test_async_connect_timeout_raises_a_timeout_error(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Surface a timeout as APITimeoutError after the whole budget is spent."""
    route = respx_mock.get(FOOD_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

    with pytest.raises(APITimeoutError):
        await async_client.foods.get(FOOD_ID)

    assert route.call_count == ATTEMPTS


@pytest.mark.anyio
async def test_async_read_timeout_is_retried_on_an_idempotent_read(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Replay an ambiguous failure on a read, exactly as the synchronous client does."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[httpx.ReadTimeout("timed out waiting for the response"), food_response()],
    )

    food = await async_client.foods.get(FOOD_ID)

    assert food.id == FOOD_ID
    assert route.call_count == 2


@pytest.mark.anyio
async def test_async_read_timeout_is_never_retried_on_a_food_log_create(
    user_async_client: AsyncJanuary, respx_mock: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Refuse to replay an ambiguous failure on a create, on every backend the SDK supports."""
    # The asynchronous half of the duplicate-log rule. This runs once on asyncio and once on trio,
    # because the retry decision must come from the request spec and not from anything the event
    # loop does.
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        side_effect=httpx.ReadTimeout("timed out waiting for the response"),
    )

    with pytest.raises(APITimeoutError):
        await user_async_client.food_logs.create(FOODS_LOGGED)

    assert route.call_count == 1
    assert recorded_sleeps == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code"), AMBIGUOUS_STATUS_CASES, ids=[code for _, code in AMBIGUOUS_STATUS_CASES]
)
async def test_async_retryable_server_error_is_never_retried_on_a_food_log_create(
    user_async_client: AsyncJanuary,
    respx_mock: respx.MockRouter,
    recorded_sleeps: list[float],
    status: int,
    code: str,
) -> None:
    """Refuse to replay a retryable 5xx on a create asynchronously too, on every backend."""
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        return_value=httpx.Response(status, json=error_body(code)),
    )

    with pytest.raises(InternalServerError):
        await user_async_client.food_logs.create(FOODS_LOGGED)

    assert route.call_count == 1
    assert recorded_sleeps == []


@pytest.mark.anyio
async def test_async_rate_limit_is_still_retried_on_a_food_log_create(
    user_async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Keep the 429 carve-out on the asynchronous client, so the two clients agree."""
    route = respx_mock.post(FOOD_LOGS_URL).mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limited")),
            httpx.Response(201, json=FOOD_LOG_PAYLOAD),
        ],
    )

    log = await user_async_client.food_logs.create(FOODS_LOGGED)

    assert log.id == FOOD_LOG_PAYLOAD["id"]
    assert route.call_count == 2


@pytest.mark.anyio
async def test_async_proxy_error_is_replayed_like_any_other_connection_failure(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Classify a rejected proxy tunnel as pre-send on the asynchronous client too."""
    route = respx_mock.get(FOOD_URL).mock(
        side_effect=[httpx.ProxyError("502 Bad Gateway from the proxy"), food_response()],
    )

    food = await async_client.foods.get(FOOD_ID)

    assert food.id == FOOD_ID
    assert route.call_count == 2
