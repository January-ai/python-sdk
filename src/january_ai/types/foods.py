"""Models for the food database: foods, servings, suggestions and healthier alternatives.

Also home to the food/serving selection shapes that food logs and glucose predictions build on.
"""

from __future__ import annotations

from typing import TypedDict

from pydantic import Field

from ._base import JanuaryModel
from .shared import DietPreference, DietRestriction, MacroNutrients, Nutrients


class FoodServing(JanuaryModel):
    """One of the portion sizes a food can be logged in."""

    id: int
    quantity: float
    unit: str
    scaling_factor: float = Field(
        description="Multiplier applied to the food's nutrition values for this serving."
    )
    weight_grams: float | None
    is_primary: bool = Field(description="Whether this is the default serving for the food.")


class Food(JanuaryModel):
    """A food from the database, with its nutrition panel and every serving it supports.

    ``brand_name`` is absent for generic (non-branded) foods.
    """

    id: int
    name: str
    brand_name: str | None = None
    nutrients: Nutrients = Field(
        description="Per-serving nutrition. Keys are omitted when the database has no value."
    )
    glycemic_index: float | None = None
    glycemic_load: float | None = None
    image_url: str | None = None
    upc: str | None = Field(
        default=None, description="The product's barcode, for branded foods that have one."
    )
    servings: list[FoodServing]


class FoodSearchResponse(JanuaryModel):
    """A page of foods matching a search query."""

    total_count: int = Field(
        description=(
            "Number of foods matching the query, which may exceed the page returned. Counted up "
            "to a ceiling of 250, so a value of 250 means '250 or more' rather than an exact "
            "total: page through with limit instead of dividing by it."
        )
    )
    items: list[Food]


class FoodSuggestion(JanuaryModel):
    """A lightweight autocomplete hit.

    Generic food names are lowercase; branded foods keep their product name, and ``brand_name``
    is absent for generic foods. ``nutrients`` carries calories per default serving only: fetch
    the food itself for the full panel.
    """

    id: int
    name: str
    brand_name: str | None = None
    image_url: str | None = None
    nutrients: Nutrients | None = None


class FoodSuggestionsResponse(JanuaryModel):
    """Autocomplete results for a partial food name."""

    items: list[FoodSuggestion] = Field(
        description=(
            "Ranked suggestions, generic foods before branded. Empty when nothing matches."
        )
    )


class FoodAlternativesRequest(JanuaryModel):
    """Request body constraining which healthier alternatives are returned."""

    diet_restrictions: list[DietRestriction] | None = Field(
        default=None,
        description="Allergens or ingredients to avoid. Omit it, or send [], if none apply.",
    )
    diet_preferences: list[DietPreference] | None = Field(
        default=None,
        description="Dietary patterns to match. Omit it, or send [], if none apply.",
    )


class ServingSummary(JanuaryModel):
    """A serving as reported alongside an alternative food: identity and unit, no scaling."""

    id: int
    quantity: float | None = None
    unit: str


class AlternativeFood(JanuaryModel):
    """The food proposed as a healthier alternative.

    ``brand_name`` is empty for generic (non-branded) foods.
    """

    id: int | None = None
    name: str
    brand_name: str | None = None
    nutrients: MacroNutrients
    servings: list[ServingSummary] | None = None


class FoodAlternative(JanuaryModel):
    """One suggested alternative to the food that was asked about."""

    food: AlternativeFood


class FoodAlternativesResponse(JanuaryModel):
    """Healthier alternatives to a food, filtered by restrictions and preferences."""

    alternatives: list[FoodAlternative] = Field(
        description=(
            "Healthier alternatives matching the restrictions and preferences. An empty array is "
            "a valid result, not an error."
        )
    )


class ServingSelection(JanuaryModel):
    """How much of one of a food's servings was consumed."""

    id: int = Field(description="One of the food's serving ids.")
    quantity: float = Field(description="How many of that serving were consumed. At most 10000.")


class FoodSelection(JanuaryModel):
    """A food plus the serving and amount of it that was eaten."""

    id: int = Field(description="Food id from a search, scan, or detection result.")
    serving: ServingSelection


class ServingSelectionParam(TypedDict):
    """Serving and amount to send when selecting a food.

    Attributes:
        id: One of the food's serving ids.
        quantity: How many of that serving were consumed. At most 10000.
    """

    id: int
    quantity: float


class FoodSelectionParam(TypedDict):
    """Food and serving to send when logging a meal or predicting a glucose response.

    Attributes:
        id: Food id from a search, scan, or detection result.
        serving: The serving and amount of it that was eaten.
    """

    id: int
    serving: ServingSelectionParam
