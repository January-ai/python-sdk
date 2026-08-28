"""Models for minting and revoking short-lived client tokens."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ._base import JanuaryModel
from .shared import Scope


class CreateClientToken(JanuaryModel):
    """Request body for minting a client token."""

    end_user_id: str = Field(
        description=(
            "Your stable ID for the end user this token acts as. The token is bound to it; "
            "requests made with the token act only on this user. At most 64 characters."
        )
    )
    scopes: list[Scope] | None = Field(
        default=None,
        description=(
            "What the token may do. Omit to grant the full client-grantable set. Grant only what "
            "the screen needs."
        ),
    )
    ttl_seconds: int | None = Field(
        default=None,
        description=(
            "How long the token stays valid, in seconds. Between 300 and 7200; defaults to 1800."
        ),
    )


class ClientTokenResponse(JanuaryModel):
    """A freshly minted client token and the terms it was minted under."""

    token: str = Field(
        description=(
            "The credential itself. Shown exactly once: it is stored only as a hash and can never "
            "be retrieved again."
        )
    )
    expires_in: int = Field(
        description=(
            "Seconds until the token expires, counted from the moment this response was produced. "
            "Compute expiry from this rather than from expires_at, since a device clock that is "
            "wrong makes an absolute timestamp wrong with it."
        )
    )
    expires_at: datetime = Field(
        description="The same expiry as an absolute UTC instant, for logs and humans."
    )
    end_user_id: str = Field(
        description=(
            "The end user this token is bound to, echoed back so a caller can assert it minted "
            "what it meant to."
        )
    )
    scopes: list[str] = Field(
        description="What this token may do, whether the scopes were requested or defaulted."
    )
