"""Minting and revoking the short-lived client tokens a device calls the API with.

Both operations authenticate with your partner API key (``sk-...``), so they belong on your
backend. A client token is what you hand to a phone: it is bound to one end user, expires on its
own, and can be revoked for that user alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import httpx

from .._base_client import AsyncAPIClient, SyncAPIClient
from .._constants import API_VERSION_PATH
from .._types import NOT_GIVEN, NotGiven, RequestSpec
from ..types import ClientTokenResponse, Scope

__all__ = ["AsyncAuth", "Auth"]

_CLIENT_TOKENS_PATH = f"{API_VERSION_PATH}/auth/client-tokens"


def _create_client_token_spec(
    end_user_id: str,
    *,
    scopes: Sequence[Scope] | None,
    ttl_seconds: int | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[ClientTokenResponse]:
    """Describe the request that mints a client token."""
    body: dict[str, object] = {"end_user_id": end_user_id}
    if scopes is not None:
        body["scopes"] = list(scopes)
    if ttl_seconds is not None:
        body["ttl_seconds"] = ttl_seconds
    return RequestSpec(
        method="POST",
        path=_CLIENT_TOKENS_PATH,
        cast_to=ClientTokenResponse,
        json_body=body,
        # The token is bound to the end user named in the body. Sending x-end-user-id as well
        # would describe a different user than the one being minted for whenever the client
        # carries a default, so the header is suppressed here.
        end_user_id=None,
        timeout=timeout,
        # Minting is not idempotent and the API accepts no idempotency key: every POST that
        # reaches the server creates another token. Replaying a request whose fate is unknown -
        # a read timeout, a dropped connection, a 502 from a gateway that had already forwarded
        # it - therefore risks minting a token the caller never sees, since the raw value is
        # returned exactly once. It would then sit valid until its TTL expires, unrevocable
        # except by revoking every token the end user holds. Same reasoning as food_logs.create.
        retry_ambiguous=False,
    )


def _revoke_client_tokens_spec(
    end_user_id: str,
    *,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[None]:
    """Describe the request that revokes every client token held by one end user."""
    return RequestSpec(
        method="DELETE",
        path=_CLIENT_TOKENS_PATH,
        params={"end_user_id": end_user_id},
        end_user_id=None,
        timeout=timeout,
        # retry_ambiguous stays at its default here, unlike on the mint above: revocation is
        # idempotent, so replaying one whose fate is unknown can only finish the job.
    )


class Auth:
    """Mint and revoke client tokens for your end users.

    Reached as ``client.auth``. Both calls require your API key, so keep them behind whatever
    login already protects your own backend: shipping the API key to a device would put a
    credential for your whole account in every copy of your app, which is the problem client
    tokens exist to solve.

    Example:
        Mint a token for one screen's worth of access and relay it to the device::

            client = January()
            token = client.auth.create_client_token(
                "acme-user-8271", scopes=["foods:read"], ttl_seconds=900
            )
            send_to_device(token.token, expires_in=token.expires_in)

        When the device reports the user signed out on a lost phone::

            client.auth.revoke_client_tokens("acme-user-8271")
    """

    def __init__(self, client: SyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    def create_client_token(
        self,
        end_user_id: str,
        *,
        scopes: Sequence[Scope] | None = None,
        ttl_seconds: int | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> ClientTokenResponse:
        """Exchange your API key for a short-lived token bound to one end user.

        The token lets your mobile app call the API directly instead of through a proxy that
        holds your key. The end user is named in the body rather than in the ``x-end-user-id``
        header: the token is bound to that identifier, and requests made with the token act only
        on that user whatever headers they carry.

        The raw token value is returned exactly once, since the API stores only a hash of it.
        Relay it to the device and let the device refresh when it expires: a ``401`` with code
        ``token_expired`` is the signal to mint a new one and retry the original request once.

        Like :meth:`~january_ai.resources.food_logs.FoodLogs.create`, this call is not replayed
        after a failure that may already have reached the server. Minting is not idempotent and
        the API accepts no idempotency key, so a replay would mint a second token whose value the
        caller never receives - valid until its TTL runs out, and clearable only by revoking every
        token the end user holds. If minting raises :class:`~january_ai.APIConnectionError` or
        :class:`~january_ai.APITimeoutError`, simply mint again; if you need certainty that no
        stray token survives, call :meth:`revoke_client_tokens` for that end user first.

        Args:
            end_user_id: Your stable identifier for the end user, at most 64 characters. Opaque
                to January.
            scopes: What the token may do. Omit to grant the full client-grantable set; grant
                only what the screen actually needs.
            ttl_seconds: How long the token stays valid, between 300 and 7200 seconds. Defaults
                to 1800 seconds server-side.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The token, its scopes, and both the relative and absolute form of its expiry.

        Raises:
            PermissionDeniedError: If client tokens are not enabled for your account, or this
                call was itself made with a client token, which cannot mint further tokens.
            APIStatusError: For any other error status the API returned.
        """
        return cast(
            ClientTokenResponse,
            self._client.send(
                _create_client_token_spec(
                    end_user_id, scopes=scopes, ttl_seconds=ttl_seconds, timeout=timeout
                )
            ),
        )

    def revoke_client_tokens(
        self,
        end_user_id: str,
        *,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> None:
        """Revoke every outstanding client token for one end user.

        The lever for a lost device or a deleted account. Revocation takes effect within 60
        seconds, the authentication cache window, so stop trusting the user in your own app as
        well for an immediate cut-off; a token minted in the same instant as the revoke may
        survive it, bounded by its own expiry.

        The call is safe to repeat. One call stops at most 500 tokens so that it cannot time out,
        and the API reports how many it actually stopped in the ``X-Revoked-Count`` header, which
        this method does not surface. If a user somehow holds more than 500 tokens, which means
        your app is minting per request rather than per session, simply call again: the operation
        is idempotent and becomes a no-op once nothing is left to revoke. To read the count
        itself, pass your own ``http_client`` with an httpx response event hook.

        Args:
            end_user_id: The end user whose tokens should all be revoked.
            timeout: A timeout for this call only, overriding the client's.

        Raises:
            APIStatusError: If the API returned an error status. A ``503`` names how many tokens
                were revoked before the failure rather than reporting a partial count as though
                it were the whole story; retry it, which only picks up the remainder.
        """
        self._client.send(_revoke_client_tokens_spec(end_user_id, timeout=timeout))


class AsyncAuth:
    """Mint and revoke client tokens for your end users, without blocking the event loop.

    Reached as ``client.auth`` on :class:`~january_ai.AsyncJanuary`. Identical to :class:`Auth`
    in arguments and results.

    Example:
        Mint a token inside a request handler on your own API::

            client = AsyncJanuary()
            token = await client.auth.create_client_token(
                "acme-user-8271", scopes=["foods:read"], ttl_seconds=900
            )
    """

    def __init__(self, client: AsyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    async def create_client_token(
        self,
        end_user_id: str,
        *,
        scopes: Sequence[Scope] | None = None,
        ttl_seconds: int | None = None,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> ClientTokenResponse:
        """Exchange your API key for a short-lived token bound to one end user.

        The token lets your mobile app call the API directly instead of through a proxy that
        holds your key. The end user is named in the body rather than in the ``x-end-user-id``
        header: the token is bound to that identifier, and requests made with the token act only
        on that user whatever headers they carry.

        The raw token value is returned exactly once, since the API stores only a hash of it.
        Relay it to the device and let the device refresh when it expires: a ``401`` with code
        ``token_expired`` is the signal to mint a new one and retry the original request once.

        Like :meth:`~january_ai.resources.food_logs.FoodLogs.create`, this call is not replayed
        after a failure that may already have reached the server. Minting is not idempotent and
        the API accepts no idempotency key, so a replay would mint a second token whose value the
        caller never receives - valid until its TTL runs out, and clearable only by revoking every
        token the end user holds. If minting raises :class:`~january_ai.APIConnectionError` or
        :class:`~january_ai.APITimeoutError`, simply mint again; if you need certainty that no
        stray token survives, call :meth:`revoke_client_tokens` for that end user first.

        Args:
            end_user_id: Your stable identifier for the end user, at most 64 characters. Opaque
                to January.
            scopes: What the token may do. Omit to grant the full client-grantable set; grant
                only what the screen actually needs.
            ttl_seconds: How long the token stays valid, between 300 and 7200 seconds. Defaults
                to 1800 seconds server-side.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The token, its scopes, and both the relative and absolute form of its expiry.

        Raises:
            PermissionDeniedError: If client tokens are not enabled for your account, or this
                call was itself made with a client token, which cannot mint further tokens.
            APIStatusError: For any other error status the API returned.
        """
        return cast(
            ClientTokenResponse,
            await self._client.send(
                _create_client_token_spec(
                    end_user_id, scopes=scopes, ttl_seconds=ttl_seconds, timeout=timeout
                )
            ),
        )

    async def revoke_client_tokens(
        self,
        end_user_id: str,
        *,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> None:
        """Revoke every outstanding client token for one end user.

        The lever for a lost device or a deleted account. Revocation takes effect within 60
        seconds, the authentication cache window, so stop trusting the user in your own app as
        well for an immediate cut-off; a token minted in the same instant as the revoke may
        survive it, bounded by its own expiry.

        The call is safe to repeat. One call stops at most 500 tokens so that it cannot time out,
        and the API reports how many it actually stopped in the ``X-Revoked-Count`` header, which
        this method does not surface. If a user somehow holds more than 500 tokens, which means
        your app is minting per request rather than per session, simply call again: the operation
        is idempotent and becomes a no-op once nothing is left to revoke. To read the count
        itself, pass your own ``http_client`` with an httpx response event hook.

        Args:
            end_user_id: The end user whose tokens should all be revoked.
            timeout: A timeout for this call only, overriding the client's.

        Raises:
            APIStatusError: If the API returned an error status. A ``503`` names how many tokens
                were revoked before the failure rather than reporting a partial count as though
                it were the whole story; retry it, which only picks up the remainder.
        """
        await self._client.send(_revoke_client_tokens_spec(end_user_id, timeout=timeout))
