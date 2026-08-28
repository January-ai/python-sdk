"""Client construction: how configuration resolves, and what is validated before anything is sent.

The rule the SDK applies everywhere is the same one three times over - an explicit argument, then
the environment variable, then the SDK default - and the payoff is that a mistake surfaces at the
line that made it rather than at the first request. These tests pin that ordering down, along with
the two things a client owes its caller once built: it never closes an HTTP client it did not
create, and it never prints the credential it was given.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from january_ai import AsyncJanuary, January, JanuaryError
from january_ai._constants import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, ENV_API_KEY, ENV_BASE_URL
from january_ai.resources import (
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

RESOURCE_NAMES = (
    "auth",
    "credits",
    "foods",
    "restaurants",
    "food_scans",
    "food_logs",
    "glucose",
)


@pytest.fixture(autouse=True)
def _unset_january_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from an environment that configures nothing.

    A developer running the suite with a real key exported would otherwise see the missing-key
    tests pass for the wrong reason, or fail outright.
    """
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    monkeypatch.delenv(ENV_BASE_URL, raising=False)


def test_the_environment_variable_names_are_the_documented_ones() -> None:
    """Pin the two environment variables the documentation tells integrators to export."""
    assert ENV_API_KEY == "JANUARY_API_KEY"
    assert ENV_BASE_URL == "JANUARY_BASE_URL"


def test_the_api_key_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the key from ``JANUARY_API_KEY`` when the constructor is given none."""
    monkeypatch.setenv(ENV_API_KEY, "sk-from-the-environment")

    with January() as client:
        assert client._client.api_key == "sk-from-the-environment"


def test_the_base_url_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the origin from ``JANUARY_BASE_URL``, which is how a staging environment is selected."""
    monkeypatch.setenv(ENV_BASE_URL, "https://staging.january.test")

    with January("sk-test") as client:
        assert str(client._client.base_url) == "https://staging.january.test"


def test_the_base_url_defaults_to_production() -> None:
    """Fall through to the production origin when nothing else names one."""
    with January("sk-test") as client:
        assert str(client._client.base_url) == DEFAULT_BASE_URL


def test_an_explicit_argument_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the constructor win over the environment for both the key and the origin."""
    monkeypatch.setenv(ENV_API_KEY, "sk-from-the-environment")
    monkeypatch.setenv(ENV_BASE_URL, "https://staging.january.test")

    with January("sk-explicit", base_url="https://proxy.january.test") as client:
        assert client._client.api_key == "sk-explicit"
        assert str(client._client.base_url) == "https://proxy.january.test"


def test_a_missing_api_key_raises_at_construction() -> None:
    """Fail while building the client, naming both ways of supplying the key."""
    with pytest.raises(JanuaryError, match=ENV_API_KEY):
        January()
    with pytest.raises(JanuaryError, match=ENV_API_KEY):
        AsyncJanuary()


def test_a_whitespace_only_api_key_counts_as_missing() -> None:
    """Treat a blank key as no key, rather than sending ``Authorization: Bearer`` with spaces."""
    with pytest.raises(JanuaryError, match=ENV_API_KEY):
        January("   ")


def test_a_whitespace_only_environment_key_counts_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply the same rule to the environment, where a stray newline is easy to introduce."""
    monkeypatch.setenv(ENV_API_KEY, "\n\t ")

    with pytest.raises(JanuaryError, match=ENV_API_KEY):
        January()


def test_surrounding_whitespace_is_stripped_from_the_api_key() -> None:
    """Strip a key pasted with a trailing newline instead of failing to authenticate."""
    with January("  sk-test-key\n") as client:
        assert client._client.api_key == "sk-test-key"


def test_a_negative_retry_budget_is_rejected() -> None:
    """Reject a negative retry budget, which has no meaning, with ``ValueError``."""
    with pytest.raises(ValueError, match="max_retries"):
        January("sk-test", max_retries=-1)
    with pytest.raises(ValueError, match="max_retries"):
        AsyncJanuary("sk-test", max_retries=-1)


def test_zero_retries_is_accepted() -> None:
    """Accept zero, which is the documented way to disable retrying."""
    with January("sk-test", max_retries=0) as client:
        assert client._client.max_retries == 0


@pytest.mark.parametrize("base_url", ["january.ai", "partners.january.ai/v1.2", "ftp://x.test"])
def test_a_base_url_without_an_http_scheme_is_rejected_at_construction(base_url: str) -> None:
    """Fail where the mistake is, rather than at the first request with an unrelated error."""
    # The constructor advertises that it validates configuration before anything is sent, and a
    # forgotten scheme is the likeliest typo of the lot: httpx accepts "january.ai" happily and
    # turns it into a relative URL, so the failure surfaced much later and pointed nowhere useful.
    with pytest.raises(ValueError, match="http"):
        January("sk-test", base_url=base_url)
    with pytest.raises(ValueError, match="http"):
        AsyncJanuary("sk-test", base_url=base_url)


def test_a_plain_http_base_url_is_still_accepted() -> None:
    """Keep http:// working: a local test server or a sidecar proxy is a legitimate origin."""
    with January("sk-test", base_url="http://127.0.0.1:8080") as client:
        assert str(client._client.base_url) == "http://127.0.0.1:8080"


def test_trailing_slashes_are_stripped_from_the_base_url() -> None:
    """Normalize the origin so appending a path cannot produce a double slash."""
    with January("sk-test", base_url="https://proxy.january.test///") as client:
        assert str(client._client.base_url) == "https://proxy.january.test"


def test_a_normalized_base_url_produces_a_clean_request_path(
    respx_mock: respx.MockRouter,
) -> None:
    """Send to a proxy's path prefix with exactly one slash joining it to the operation path."""
    route = respx_mock.get("https://proxy.january.test/january/v1.2/credits").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan": "free",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "resets_at": "2026-09-01T00:00:00.000Z",
                "used_credits": 1,
            },
        )
    )

    with January("sk-test", base_url="https://proxy.january.test/january/") as client:
        client.credits.get()

    assert route.calls.last.request.url.path == "/january/v1.2/credits"


def test_a_float_timeout_becomes_an_httpx_timeout() -> None:
    """Accept a plain number of seconds and apply it to every phase."""
    with January("sk-test", timeout=2.5) as client:
        assert client._client.timeout == httpx.Timeout(2.5)
        assert client._client.timeout.read == 2.5
        assert client._client.timeout.connect == 2.5


def test_an_httpx_timeout_is_kept_as_given() -> None:
    """Pass a fully specified timeout through untouched, for per-phase control."""
    supplied = httpx.Timeout(30.0, connect=1.0)

    with January("sk-test", timeout=supplied) as client:
        assert client._client.timeout == supplied


def test_no_timeout_selects_the_sdk_default() -> None:
    """Use the SDK's own 60-second timeout with a 5-second connect budget when none is given."""
    with January("sk-test") as client:
        assert client._client.timeout == DEFAULT_TIMEOUT


def test_the_context_manager_closes_the_owned_http_client() -> None:
    """Release the connection pool the client built for itself on leaving the context."""
    with January("sk-test") as client:
        transport = client._client._client
        assert transport.is_closed is False

    assert transport.is_closed is True


def test_close_leaves_a_supplied_http_client_open() -> None:
    """Never close an ``httpx.Client`` the caller passed in: it may be shared with their app."""
    supplied = httpx.Client()

    client = January("sk-test", http_client=supplied)
    client.close()

    assert supplied.is_closed is False
    assert client._client._client is supplied
    supplied.close()


def test_close_is_idempotent() -> None:
    """Tolerate a second ``close()``, which a context manager plus an explicit call produces."""
    client = January("sk-test")
    client.close()
    client.close()

    assert client._client._client.is_closed is True


@pytest.mark.anyio
async def test_the_async_context_manager_closes_the_owned_http_client() -> None:
    """Release the asynchronous pool the same way on leaving the context."""
    async with AsyncJanuary("sk-test") as client:
        transport = client._client._client
        assert transport.is_closed is False

    assert transport.is_closed is True


@pytest.mark.anyio
async def test_aclose_leaves_a_supplied_async_http_client_open() -> None:
    """Never close an ``httpx.AsyncClient`` the caller passed in."""
    supplied = httpx.AsyncClient()

    client = AsyncJanuary("sk-test", http_client=supplied)
    await client.aclose()

    assert supplied.is_closed is False
    await supplied.aclose()


def test_repr_does_not_leak_the_api_key() -> None:
    """Reduce the credential to its prefix, so a client in a traceback reveals nothing usable."""
    secret = "sk-live-9f2b7c41d8e6a05314ab"

    client = January(secret, base_url="https://proxy.january.test")
    rendered = repr(client)
    client.close()

    assert secret not in rendered
    assert "9f2b7c41d8e6a05314ab" not in rendered
    assert "sk-***" in rendered
    assert "https://proxy.january.test" in rendered


def test_async_repr_does_not_leak_the_api_key() -> None:
    """Apply the same redaction to the asynchronous client, including a client token."""
    secret = "ct-4fQr7yNb2KcXm9TvLpZ3wHs6JdRg8AeYuQ1oViB5xCn"

    rendered = repr(AsyncJanuary(secret))

    assert secret not in rendered
    assert "ct-***" in rendered
    assert rendered.startswith("AsyncJanuary(")


def test_the_sync_client_exposes_all_seven_resources() -> None:
    """Expose every resource group as a plain attribute, constructed eagerly."""
    with January("sk-test") as client:
        for name in RESOURCE_NAMES:
            assert hasattr(client, name), name
        assert isinstance(client.auth, Auth)
        assert isinstance(client.credits, Credits)
        assert isinstance(client.foods, Foods)
        assert isinstance(client.restaurants, Restaurants)
        assert isinstance(client.food_scans, FoodScans)
        assert isinstance(client.food_logs, FoodLogs)
        assert isinstance(client.glucose, Glucose)


def test_the_async_client_exposes_all_seven_resources() -> None:
    """Expose the same seven groups on the asynchronous client, in their async forms."""
    client = AsyncJanuary("sk-test")

    for name in RESOURCE_NAMES:
        assert hasattr(client, name), name
    assert isinstance(client.auth, AsyncAuth)
    assert isinstance(client.credits, AsyncCredits)
    assert isinstance(client.foods, AsyncFoods)
    assert isinstance(client.restaurants, AsyncRestaurants)
    assert isinstance(client.food_scans, AsyncFoodScans)
    assert isinstance(client.food_logs, AsyncFoodLogs)
    assert isinstance(client.glucose, AsyncGlucose)


def test_the_default_end_user_is_kept_on_the_client() -> None:
    """Record a single-user client's identifier so calls need not repeat it."""
    with January("sk-test", default_end_user_id="acme-user-8271") as client:
        assert client._client.default_end_user_id == "acme-user-8271"


def test_default_headers_are_kept_on_the_client() -> None:
    """Keep caller-supplied headers for merging into every request."""
    with January("sk-test", default_headers={"X-Trace": "abc"}) as client:
        assert client._client.default_headers == {"X-Trace": "abc"}
