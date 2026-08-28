"""Reading the credit allowance and consumption of the current billing period."""

from __future__ import annotations

from typing import cast

import httpx

from .._base_client import AsyncAPIClient, SyncAPIClient
from .._constants import API_VERSION_PATH
from .._types import NOT_GIVEN, NotGiven, RequestSpec
from ..types import CreditsResponse

__all__ = ["AsyncCredits", "Credits"]

_CREDITS_PATH = f"{API_VERSION_PATH}/credits"


def _get_credits_spec(
    *,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[CreditsResponse]:
    """Describe the request that reads the current credit balance."""
    return RequestSpec(
        method="GET",
        path=_CREDITS_PATH,
        cast_to=CreditsResponse,
        end_user_id=end_user_id,
        timeout=timeout,
    )


class Credits:
    """Read how much of your credit allowance is left.

    Reached as ``client.credits``.

    Nearly every successful v1.2 API call costs one credit; requests that fail cost nothing, and
    v1.1 calls are not counted. Three operations were measured against the live API and billed
    nothing: :meth:`get` itself, which always answers including once the allowance is exhausted,
    and both :class:`~january_ai.resources.auth.Auth` operations - minting and revoking client
    tokens. That was observed rather than promised by the published spec, so treat it as how the
    API behaves today and not as a contract: bill your own users off your own accounting rather
    than off an assumption about which January calls are free.

    Example:
        Warn before a batch job runs into the ceiling::

            balance = client.credits.get()
            if balance.remaining_credits is not None and balance.remaining_credits < 1000:
                logging.warning("%s credits left until %s", balance.remaining_credits,
                                balance.resets_at)
    """

    def __init__(self, client: SyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    def get(
        self,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> CreditsResponse:
        """Get the credit allowance and consumption for the current calendar month (UTC).

        ``included_credits`` and ``remaining_credits`` come back as ``None`` on a plan with no
        ceiling. When credits do run out, other v1.2 endpoints answer ``429`` with code
        ``credit_limit_exceeded`` until the allowance resets, which the SDK raises as
        :class:`~january_ai.CreditLimitExceededError` and never retries, because retrying cannot
        help before the reset.

        This call is not billed, and neither are the two ``auth`` operations - minting and revoking
        a client token. That was measured against the live API rather than promised by the published
        spec, so do not hard-code it: meter your own usage against this endpoint, which reports the
        number January is actually charging you.

        Args:
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The plan, the period boundaries, when the allowance resets, and the credits included,
            used, and remaining.
        """
        return cast(
            CreditsResponse,
            self._client.send(_get_credits_spec(end_user_id=end_user_id, timeout=timeout)),
        )


class AsyncCredits:
    """Read how much of your credit allowance is left, without blocking the event loop.

    Reached as ``client.credits`` on :class:`~january_ai.AsyncJanuary`. Identical to
    :class:`Credits` in arguments and results.

    Example:
        Check the balance alongside other startup work::

            balance = await client.credits.get()
    """

    def __init__(self, client: AsyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    async def get(
        self,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> CreditsResponse:
        """Get the credit allowance and consumption for the current calendar month (UTC).

        ``included_credits`` and ``remaining_credits`` come back as ``None`` on a plan with no
        ceiling. When credits do run out, other v1.2 endpoints answer ``429`` with code
        ``credit_limit_exceeded`` until the allowance resets, which the SDK raises as
        :class:`~january_ai.CreditLimitExceededError` and never retries, because retrying cannot
        help before the reset.

        This call is not billed, and neither are the two ``auth`` operations - minting and revoking
        a client token. That was measured against the live API rather than promised by the published
        spec, so do not hard-code it: meter your own usage against this endpoint, which reports the
        number January is actually charging you.

        Args:
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The plan, the period boundaries, when the allowance resets, and the credits included,
            used, and remaining.
        """
        return cast(
            CreditsResponse,
            await self._client.send(_get_credits_spec(end_user_id=end_user_id, timeout=timeout)),
        )
