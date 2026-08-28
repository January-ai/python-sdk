"""Models for recognising a meal from a photo or a sentence, and correcting the result."""

from __future__ import annotations

from pydantic import Field

from ._base import JanuaryModel
from .shared import MacroNutrients


class ScanPhoto(JanuaryModel):
    """Request body for a photo scan."""

    image: str = Field(
        description=(
            "The meal photo, as an http(s) URL or a base64 data URI. Formats: JPG, PNG, WEBP and "
            "non-animated GIF. A URL must be publicly fetchable server-side, so hosts that block "
            "hotlinking or require a login cannot be read. Prefer the URL when the image is "
            "already hosted: base64 inflates the payload by about 33%, and request bodies over "
            "5 MB are rejected, so keep raw images under about 3.5 MB when encoding."
        )
    )


class ScanText(JanuaryModel):
    """Request body for a text scan."""

    text: str = Field(
        description=(
            "Natural-language description of what was eaten; parsed into detected foods with "
            "quantities. At most 512 characters."
        )
    )


class DetectionServing(JanuaryModel):
    """The serving a detection was measured in."""

    id: int
    quantity: float | None = None
    unit: str
    selected_quantity: float | None = Field(
        default=None,
        description=(
            "Quantity the parser selected from the text ('2 cups' becomes 2); text scans only. "
            "Advisory: corrections reads the serving's own quantity."
        ),
    )


class DetectionFood(JanuaryModel):
    """The food a detection resolved to.

    ``brand_name`` is empty for generic (non-branded) foods.
    """

    id: int | None = None
    name: str
    brand_name: str | None = None
    nutrients: MacroNutrients
    servings: list[DetectionServing] = Field(
        description="Never empty: every detection producer guarantees at least one serving."
    )


class Detection(JanuaryModel):
    """One food recognised in a scan."""

    confidence_score: str | None = Field(
        default=None,
        description="One of high, medium or low. Photo scans only; absent on text scans.",
    )
    food: DetectionFood


class ScanResult(JanuaryModel):
    """Everything recognised in one scan, with the meal totals."""

    meal_name: str | None = None
    total_nutrients: MacroNutrients | None = Field(
        default=None, description="Aggregated nutrition across all detections."
    )
    detections: list[Detection] = Field(
        description="Detected foods. Always present: an empty array means nothing was recognized."
    )


class CorrectScan(JanuaryModel):
    """Request body for correcting a scan in plain English."""

    meal_name: str | None = Field(
        default=None,
        description=(
            "The meal name from the scan, when it returned one (photo scans do; text scans "
            "don't). Defaults to 'Meal'."
        ),
    )
    detections: list[Detection] = Field(
        description=(
            "The detections array from a photo or text food scan, exactly as returned. Omitted "
            "zero-value nutrient keys are filled in automatically; each detection needs at least "
            "one serving."
        )
    )
    user_input: str = Field(description="Plain-English description of what to correct.")
