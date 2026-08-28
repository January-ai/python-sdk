"""Vocabulary shared across every January AI domain.

Holds the API error envelope, the nutrient models that foods, menu items, scans and food logs
all speak, and the closed sets of string values the API accepts on request bodies and query
strings.

Response models never use these literals: an enum-ish field that comes back from the server is
typed :class:`str` so that a value added after this SDK was released still parses.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ._base import JanuaryModel

Scope = Literal[
    "foods:read",
    "food_scans:write",
    "food_logs:read",
    "food_logs:write",
    "glucose:read",
    "restaurants:read",
]
"""A capability a client token may be granted."""

DietRestriction = Literal[
    "gluten",
    "lactose",
    "yeast",
    "tree_nuts",
    "peanuts",
    "dairy",
    "eggs",
    "sulfites",
    "soy",
    "wheat",
    "shellfish",
    "fish",
    "mushrooms",
    "sesame",
    "msg",
    "caffeine",
    "fodmaps",
]
"""An allergen or ingredient to avoid when suggesting alternatives."""

DietPreference = Literal[
    "vegetarian",
    "vegan",
    "keto",
    "paleo",
    "pescatarian",
    "low_carbohydrate",
    "high_protein",
    "kosher",
    "halal",
]
"""A dietary pattern to match when suggesting alternatives."""

FoodCategory = Literal["general", "branded", "recipe"]
"""Which slice of the food database a search covers."""

AutocompleteCategory = Literal["general", "branded"]
"""Which slice of the food database autocomplete covers. Narrower than :data:`FoodCategory`."""

Sex = Literal["male", "female"]
"""Biological sex, as consumed by the glucose prediction model."""

ActivityLevel = Literal["sedentary", "lightly_active", "moderately_active", "very_active"]
"""How active the end user is, as consumed by the glucose prediction model."""

HealthCondition = Literal["type_2_diabetes", "prediabetes"]
"""A glucose-relevant condition. Type 1 diabetes is not supported by the prediction model."""

HeightUnit = Literal["in", "cm"]
"""Unit a height is expressed in."""

WeightUnit = Literal["lb", "kg"]
"""Unit a weight is expressed in."""


class ApiError(JanuaryModel):
    """The body of every non-2xx response."""

    message: str = Field(
        description="A developer-facing explanation of what went wrong and how to fix it."
    )
    code: str = Field(
        description=(
            "A stable machine-readable identifier for the class of failure. Build retry logic on "
            "this, never on message wording. Only rate_limited, internal_error, upstream_error, "
            "service_unavailable and upstream_timeout are safe to retry with backoff; "
            "not_implemented is permanent until the feature ships. New codes may be added over "
            "time, so treat an unknown code according to its HTTP status class."
        )
    )
    docs_url: str = Field(description="Link to the developer documentation.")


class NutrientAmount(JanuaryModel):
    """A single nutrient reading: how much, in which unit."""

    value: float
    unit: str = Field(description="Canonical across the API: g, mg, kcal, IU.")


class Nutrients(JanuaryModel):
    """The full nutrient panel. Keys are omitted when the source has no value for them."""

    calories: NutrientAmount | None = None
    protein: NutrientAmount | None = None
    carbohydrates: NutrientAmount | None = None
    net_carbohydrates: NutrientAmount | None = None
    total_fat: NutrientAmount | None = None
    trans_fat: NutrientAmount | None = None
    saturated_fat: NutrientAmount | None = None
    fiber: NutrientAmount | None = None
    total_sugars: NutrientAmount | None = None
    added_sugars: NutrientAmount | None = None
    cholesterol: NutrientAmount | None = None
    calcium: NutrientAmount | None = None
    iron: NutrientAmount | None = None
    potassium: NutrientAmount | None = None
    sodium: NutrientAmount | None = None
    vitamin_d: NutrientAmount | None = None


class MacroNutrients(JanuaryModel):
    """The macro-level nutrient panel returned by scans and alternatives.

    A subset of :class:`Nutrients`: no micronutrients, no trans fat, no cholesterol. Keys are
    omitted when the source has no value for them.
    """

    calories: NutrientAmount | None = None
    protein: NutrientAmount | None = None
    carbohydrates: NutrientAmount | None = None
    net_carbohydrates: NutrientAmount | None = None
    total_fat: NutrientAmount | None = None
    saturated_fat: NutrientAmount | None = None
    fiber: NutrientAmount | None = None
    total_sugars: NutrientAmount | None = None
    added_sugars: NutrientAmount | None = None
    sodium: NutrientAmount | None = None
