"""Minting and revoking client tokens.

Both operations are API-key only, and both deliberately refuse to send ``x-end-user-id``: the token
is bound to the end user named in the body or the query string, so a header naming a different user
would describe something the request is not doing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

import httpx
import pytest
import respx

from january_ai import AsyncJanuary, January
from january_ai.types import ClientTokenResponse

BASE_URL = "https://partners.january.ai"
CLIENT_TOKENS_URL = f"{BASE_URL}/v1.2/auth/client-tokens"

TOKEN_PAYLOAD = {
    "token": "ct-4fQr7yNb2KcXm9TvLpZ3wHs6JdRg8AeYuQ1oViB5xCn",
    "expires_in": 1800,
    "expires_at": "2026-08-26T18:35:11.000Z",
    "end_user_id": "acme-user-8271",
    "scopes": [
        "foods:read",
        "food_scans:write",
        "food_logs:read",
        "food_logs:write",
        "glucose:read",
        "restaurants:read",
    ],
}


def _fingerprint(request: httpx.Request) -> tuple[str, str, bytes]:
    """Reduce a request to the parts the synchronous and asynchronous clients must agree on."""
    return (request.method, str(request.url), request.content)


@pytest.mark.anyio
async def test_create_client_token_posts_the_end_user_and_its_terms(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Send the end user, scopes, and TTL in the body, and parse the minted token back."""
    route = respx_mock.post(CLIENT_TOKENS_URL).mock(
        return_value=httpx.Response(201, json=TOKEN_PAYLOAD)
    )

    token = client.auth.create_client_token(
        "acme-user-8271", scopes=["foods:read", "food_logs:write"], ttl_seconds=900
    )

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path == "/v1.2/auth/client-tokens"
    assert not request.url.params
    assert json.loads(request.content) == {
        "end_user_id": "acme-user-8271",
        "scopes": ["foods:read", "food_logs:write"],
        "ttl_seconds": 900,
    }

    assert isinstance(token, ClientTokenResponse)
    assert token.token == "ct-4fQr7yNb2KcXm9TvLpZ3wHs6JdRg8AeYuQ1oViB5xCn"
    assert token.expires_in == 1800
    assert token.expires_at == datetime(2026, 8, 26, 18, 35, 11, tzinfo=timezone.utc)
    assert token.end_user_id == "acme-user-8271"
    assert token.scopes[0] == "foods:read"

    await async_client.auth.create_client_token(
        "acme-user-8271", scopes=["foods:read", "food_logs:write"], ttl_seconds=900
    )
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_create_client_token_omits_scopes_and_ttl_when_not_given(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Send only the end user, leaving the API's own scope and TTL defaults in force."""
    route = respx_mock.post(CLIENT_TOKENS_URL).mock(
        return_value=httpx.Response(201, json=TOKEN_PAYLOAD)
    )

    client.auth.create_client_token("acme-user-8271")

    assert json.loads(route.calls.last.request.content) == {"end_user_id": "acme-user-8271"}


def test_create_client_token_never_sends_the_end_user_header(
    make_client: Callable[..., January], respx_mock: respx.MockRouter
) -> None:
    """Suppress ``x-end-user-id`` even when the client carries a default for it.

    The minted token is bound to the identifier in the body, so a header naming the client's
    default user would contradict the request it is attached to.
    """
    route = respx_mock.post(CLIENT_TOKENS_URL).mock(
        return_value=httpx.Response(201, json=TOKEN_PAYLOAD)
    )

    with make_client(default_end_user_id="acme-user-0001") as scoped:
        scoped.auth.create_client_token("acme-user-8271")

    assert "x-end-user-id" not in route.calls.last.request.headers


@pytest.mark.anyio
async def test_revoke_client_tokens_deletes_by_query_and_returns_none(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Name the end user in the query string and return nothing at all from the 204."""
    route = respx_mock.delete(CLIENT_TOKENS_URL).mock(return_value=httpx.Response(204))

    # The method is annotated ``-> None``, so mypy refuses to bind its result to a name; the
    # ignore is what lets the test assert the runtime value a 204 actually produces.
    result = client.auth.revoke_client_tokens("acme-user-8271")  # type: ignore[func-returns-value]

    request = route.calls.last.request
    assert request.method == "DELETE"
    assert request.url.path == "/v1.2/auth/client-tokens"
    assert dict(request.url.params) == {"end_user_id": "acme-user-8271"}
    assert request.content == b""
    assert result is None

    async_result = await async_client.auth.revoke_client_tokens(  # type: ignore[func-returns-value]
        "acme-user-8271"
    )
    assert async_result is None
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_revoke_client_tokens_never_sends_the_end_user_header(
    make_client: Callable[..., January], respx_mock: respx.MockRouter
) -> None:
    """Identify the end user in the query string only, never in the header."""
    route = respx_mock.delete(CLIENT_TOKENS_URL).mock(return_value=httpx.Response(204))

    with make_client(default_end_user_id="acme-user-0001") as scoped:
        scoped.auth.revoke_client_tokens("acme-user-8271")

    assert "x-end-user-id" not in route.calls.last.request.headers
