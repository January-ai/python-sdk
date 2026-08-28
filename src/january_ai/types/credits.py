"""Models for the credit allowance of the current billing period."""

from __future__ import annotations

from pydantic import Field

from ._base import JanuaryModel


class CreditsResponse(JanuaryModel):
    """The plan's credit allowance and consumption for the current billing period.

    ``included_credits`` and ``remaining_credits`` are absent when the plan has no ceiling.
    """

    plan: str = Field(description="The plan this allowance comes from.")
    period_start: str = Field(
        description="First day of the current billing period (UTC), inclusive, as YYYY-MM-DD."
    )
    period_end: str = Field(
        description="Last day of the current billing period (UTC), inclusive, as YYYY-MM-DD."
    )
    resets_at: str = Field(
        description="When the allowance resets and used_credits returns to 0, as an ISO instant."
    )
    included_credits: int | None = None
    used_credits: int = Field(
        description=(
            "Credits used so far this period. Nearly every successful v1.2 API call costs 1 "
            "credit; failed calls cost nothing. This endpoint and the two auth endpoints were "
            "measured as unbilled, which is observed behaviour rather than a documented "
            "guarantee."
        )
    )
    remaining_credits: int | None = None
