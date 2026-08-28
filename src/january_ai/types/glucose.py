"""Models for predicting the glucose response to a meal."""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from pydantic import Field

from ._base import JanuaryModel
from .foods import FoodSelection, ServingSelection, ServingSelectionParam
from .shared import ActivityLevel, HealthCondition, HeightUnit, Sex, WeightUnit


class Height(JanuaryModel):
    """An end user's height."""

    value: float
    unit: HeightUnit


class Weight(JanuaryModel):
    """An end user's weight."""

    value: float
    unit: WeightUnit


class GlucoseUserProfile(JanuaryModel):
    """The end user characteristics the prediction model is conditioned on."""

    age: int
    sex: Sex = Field(description="Biological sex, as consumed by the prediction model.")
    height: Height
    weight: Weight
    activity_level: ActivityLevel | None = None
    health_conditions: list[HealthCondition] | None = Field(
        default=None,
        description=(
            "Omit it, or send [], if none apply. Type 1 diabetes is not supported by the "
            "prediction model."
        ),
    )


class CgmReading(JanuaryModel):
    """A single continuous glucose monitor reading."""

    timestamp: datetime = Field(
        description="When the reading was taken. Must carry a timezone designator."
    )
    value: float = Field(description="mg/dL. At most one reading per 15-minute window.")


class ConsumedFoodEntry(JanuaryModel):
    """A meal eaten during the CGM history, used to personalize the prediction."""

    timestamp: datetime = Field(
        description="When the food was eaten. Must carry a timezone designator."
    )
    id: int
    serving: ServingSelection


class GlucosePredict(JanuaryModel):
    """Request body for a glucose prediction.

    ``cgm_data`` and ``consumed_foods`` are optional but go together: each requires the other.
    """

    user_profile: GlucoseUserProfile
    foods: list[FoodSelection] = Field(
        description="The meal to predict the glucose response for. At most 100 foods."
    )
    start_time: datetime = Field(
        description="When the meal is (or will be) eaten. Must carry a timezone designator."
    )
    cgm_data: list[CgmReading] | None = Field(
        default=None,
        description="Optional CGM history for personalization; requires consumed_foods.",
    )
    consumed_foods: list[ConsumedFoodEntry] | None = Field(
        default=None,
        description="The meals eaten during the CGM history; requires cgm_data.",
    )


class GlucosePredictionPoint(JanuaryModel):
    """One point on the predicted glucose curve."""

    # The spec types this `number`, not `integer`, as it does every numeric field in the API. It is
    # narrowed here on purpose: the curve is documented as 15-minute intervals, and `int` is what
    # reads correctly at a call site. A fractional value would fail validation rather than
    # truncate, and would arrive as APIResponseValidationError telling the caller to upgrade.
    minutes: int = Field(description="Minutes after start_time.")
    value: float = Field(description="Predicted glucose, mg/dL.")


class GlucoseChart(JanuaryModel):
    """Suggested Y-axis bounds for plotting the curve. Not the extremes of the curve itself."""

    min: float = Field(
        description="Suggested Y-axis lower bound (mg/dL). A fixed target-range bound."
    )
    max: float = Field(
        description=(
            "Suggested Y-axis upper bound (mg/dL): 180 with Type 2 diabetes in "
            "health_conditions, otherwise 140."
        )
    )


class GlucosePredictionResponse(JanuaryModel):
    """The predicted glucose response to a meal."""

    prediction: list[GlucosePredictionPoint] = Field(
        description="The predicted glucose curve at 15-minute intervals, starting at start_time."
    )
    impact_score: str = Field(
        description="The meal's overall glucose impact: one of low, medium or high."
    )
    chart: GlucoseChart


class HeightParam(TypedDict):
    """Height to send in a glucose prediction request.

    Attributes:
        value: The magnitude, in ``unit``.
        unit: Either ``in`` or ``cm``.
    """

    value: float
    unit: HeightUnit


class WeightParam(TypedDict):
    """Weight to send in a glucose prediction request.

    Attributes:
        value: The magnitude, in ``unit``.
        unit: Either ``lb`` or ``kg``.
    """

    value: float
    unit: WeightUnit


class _GlucoseUserProfileRequired(TypedDict):
    age: int
    sex: Sex
    height: HeightParam
    weight: WeightParam


class GlucoseUserProfileParam(_GlucoseUserProfileRequired, total=False):
    """End user characteristics to send in a glucose prediction request.

    ``age``, ``sex``, ``height`` and ``weight`` are required; the rest may be omitted.

    Attributes:
        activity_level: How active the end user is.
        health_conditions: Omit, or send [], if none apply. Type 1 diabetes is not supported by
            the prediction model.
    """

    activity_level: ActivityLevel
    health_conditions: list[HealthCondition]


class CgmReadingParam(TypedDict):
    """A CGM reading to send in a glucose prediction request.

    Attributes:
        timestamp: When the reading was taken. A ``datetime`` must be timezone-aware; a string
            must carry a timezone designator.
        value: mg/dL. At most one reading per 15-minute window.
    """

    timestamp: str | datetime
    value: float


class ConsumedFoodEntryParam(TypedDict):
    """A previously eaten meal to send alongside CGM history.

    Attributes:
        timestamp: When the food was eaten. A ``datetime`` must be timezone-aware; a string must
            carry a timezone designator.
        id: Food id from a search, scan, or detection result.
        serving: The serving and amount of it that was eaten.
    """

    timestamp: str | datetime
    id: int
    serving: ServingSelectionParam
