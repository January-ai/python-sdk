"""Mint a short-lived client token for one end user, use it, and revoke it.

This is the pattern for calling January from a phone or a browser. Your `sk-` key authenticates
your whole account and must never ship inside an app; a client token is bound to exactly one end
user, carries only the scopes you grant it, and expires within two hours. Mint it on your backend,
behind whatever login already protects your own APIs, and relay it to the device.

Demonstrates `auth.create_client_token`, using the returned token as an ordinary API key, and
`auth.revoke_client_tokens`.

Usage:
    export JANUARY_API_KEY=sk-...
    python examples/04_client_tokens.py

Cost: 3 credits - mint, one call made with the token, revoke. Failed calls cost nothing.
"""

from __future__ import annotations

import os

from january_ai import AuthenticationError, January, JanuaryError, PermissionDeniedError
from january_ai.types import Scope

END_USER_ID = "demo-user-1042"

# Grant the narrowest set the screen needs. Omitting `scopes` grants the full client-grantable set.
SCOPES: list[Scope] = ["foods:read", "food_scans:write"]


def mint_for_device(backend: January, end_user_id: str) -> str:
    """Mint a token on the backend and return the value to relay to the device.

    In a real service this is an authenticated endpoint of your own: the caller proves who they are
    against your login, and you mint a token for that user and nobody else.
    """
    token = backend.auth.create_client_token(end_user_id, scopes=SCOPES, ttl_seconds=1800)

    print(f"minted for {token.end_user_id}")
    print(f"  scopes:     {', '.join(token.scopes)}")
    print(f"  expires in: {token.expires_in} seconds")
    print(f"  expires at: {token.expires_at.isoformat()}")
    print(f"  prefix:     {token.token[:3]}...")

    # Prefer expires_in over expires_at when scheduling a refresh on a device: a wrong device clock
    # makes an absolute timestamp wrong with it. The raw value is returned exactly once - it is
    # stored only as a hash and can never be retrieved again.
    return token.token


def main() -> int:
    """Mint a token, call the API with it, then revoke every token the user holds."""
    if os.environ.get("JANUARY_API_KEY") is None:
        print("set JANUARY_API_KEY to an sk- key from https://dashboard.january.ai")
        return 2

    with January() as backend:
        try:
            relayed = mint_for_device(backend, END_USER_ID)
        except PermissionDeniedError:
            print("client tokens are not enabled for this account, or this key is itself a token")
            return 1
        except JanuaryError as exc:
            print(f"minting failed: {exc}")
            return 1

        # On the device, the token is just an API key. Pass it positionally rather than putting it
        # in the environment: it is short-lived and belongs to one user.
        with January(relayed) as device:
            print(f"\ndevice client: {device!r}")
            try:
                results = device.foods.search("banana", limit=3)
                for food in results.items:
                    print(f"  {food.id:>10}  {food.name}")
            except AuthenticationError:
                # A 401 is the device's signal to ask the backend for a fresh token and retry once.
                print("  token expired; mint another and retry the original request")

        # The lever for a lost device or a deleted account. Safe to call repeatedly, and revocation
        # takes effect within 60 seconds - the authentication cache window.
        backend.auth.revoke_client_tokens(END_USER_ID)
        print(f"\nrevoked every outstanding token for {END_USER_ID}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
