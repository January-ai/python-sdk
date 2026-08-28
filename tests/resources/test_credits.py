"""Reading the credit allowance of the current billing period."""

from __future__ import annotations

import httpx
import pytest
import respx

from january_ai import AsyncJanuary, January
from january_ai.types import CreditsResponse

BASE_URL = "https://partners.january.ai"
CREDITS_URL = f"{BASE_URL}/v1.2/credits"

CREDITS_PAYLOAD = {
    "plan": "free",
    "period_start": "2026-08-01",
    "period_end": "2026-08-31",
    "resets_at": "2026-09-01T00:00:00.000Z",
    "included_credits": 1000,
    "used_credits": 342,
    "remaining_credits": 658,
}

# A plan with no ceiling omits both credit counts rather than sending null or zero.
UNMETERED_PAYLOAD = {
    "plan": "enterprise",
    "period_start": "2026-08-01",
    "period_end": "2026-08-31",
    "resets_at": "2026-09-01T00:00:00.000Z",
    "used_credits": 91_204,
}


def _fingerprint(request: httpx.Request) -> tuple[str, str, bytes]:
    """Reduce a request to the parts the synchronous and asynchronous clients must agree on."""
    return (request.method, str(request.url), request.content)


@pytest.mark.anyio
async def test_get_credits_reads_the_current_period(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """GET the credits endpoint with no query string and parse the whole allowance."""
    route = respx_mock.get(CREDITS_URL).mock(return_value=httpx.Response(200, json=CREDITS_PAYLOAD))

    credits = client.credits.get()

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v1.2/credits"
    assert not request.url.params
    assert request.content == b""

    assert isinstance(credits, CreditsResponse)
    assert credits.plan == "free"
    assert credits.period_start == "2026-08-01"
    assert credits.included_credits == 1000
    assert credits.used_credits == 342
    assert credits.remaining_credits == 658

    await async_client.credits.get()
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_get_credits_reports_no_ceiling_as_none(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Report an unmetered plan's absent counts as ``None`` rather than zero."""
    respx_mock.get(CREDITS_URL).mock(return_value=httpx.Response(200, json=UNMETERED_PAYLOAD))

    credits = client.credits.get()

    assert credits.plan == "enterprise"
    assert credits.included_credits is None
    assert credits.remaining_credits is None
    assert credits.used_credits == 91_204


def test_get_credits_sends_the_end_user_header_when_named(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Attribute the call to an end user when one is passed."""
    route = respx_mock.get(CREDITS_URL).mock(return_value=httpx.Response(200, json=CREDITS_PAYLOAD))

    client.credits.get(end_user_id="acme-user-8271")

    assert route.calls.last.request.headers["x-end-user-id"] == "acme-user-8271"


def test_get_credits_omits_the_end_user_header_for_an_explicit_none(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Send no end user at all when the caller passes ``None`` explicitly."""
    route = respx_mock.get(CREDITS_URL).mock(return_value=httpx.Response(200, json=CREDITS_PAYLOAD))

    client.credits.get(end_user_id=None)

    assert "x-end-user-id" not in route.calls.last.request.headers
