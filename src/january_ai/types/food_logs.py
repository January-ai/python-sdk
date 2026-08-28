"""Models for the end user's meal history: creating, reading, updating and deleting food logs."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ._base import JanuaryModel
from .foods import FoodSelection, ServingSelection
from .shared import Nutrients


class CreateFoodLog(JanuaryModel):
    """Request body for logging a meal. At most 100 foods."""

    foods: list[FoodSelection]
    timestamp_utc: datetime | None = Field(
        default=None,
        description=(
            "When the meal was eaten; any ISO-8601 offset, stored and returned in UTC. Omitted "
            "means now."
        ),
    )
    name: str | None = Field(default=None, description="At most 256 characters.")


class UpdateFoodLog(JanuaryModel):
    """Request body for a partial update to a logged meal.

    Every field is optional; whatever is sent replaces what is stored. At most 100 foods.
    """

    foods: list[FoodSelection] | None = None
    timestamp_utc: str | None = Field(
        default=None, description="UTC consumption time, ending in Z."
    )
    name: str | None = Field(default=None, description="At most 256 characters.")


class ServingDetails(JanuaryModel):
    """The serving definition a logged quantity refers to."""

    id: int
    quantity: float
    unit: str
    weight_grams: float | None = None


class LoggedFood(JanuaryModel):
    """One food inside a logged meal, resolved against the database at log time."""

    id: int
    name: str
    brand_name: str | None = None
    image_url: str | None = None
    glycemic_index: float | None = None
    glycemic_load: float | None = None
    nutrients: Nutrients = Field(description="Scaled to the consumed serving.")
    consumed_serving: ServingSelection = Field(description="What was logged.")
    serving_details: ServingDetails = Field(
        description="The serving definition the quantity refers to."
    )


class FoodLog(JanuaryModel):
    """A logged meal."""

    id: str = Field(description="Save this id to update or delete the log.")
    foods: list[LoggedFood]
    timestamp_utc: datetime
    name: str | None = None


class FoodLogListResponse(JanuaryModel):
    """Logged meals in a date range."""

    total_count: int
    items: list[FoodLog] = Field(
        description="Logs in the range, ordered by timestamp. An empty list is a valid result."
    )


class DeleteFoodLogResponse(JanuaryModel):
    """The outcome of deleting a logged meal."""

    status: str = Field(
        description=(
            "Deletion is idempotent: an unknown or already-deleted log_id returns the same "
            "response."
        )
    )
