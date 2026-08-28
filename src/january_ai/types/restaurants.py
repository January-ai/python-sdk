"""Models for nearby restaurants and the dishes on their menus."""

from __future__ import annotations

from pydantic import Field

from ._base import JanuaryModel
from .foods import FoodServing
from .shared import Nutrients


class RestaurantResult(JanuaryModel):
    """A restaurant matching a name search, or a dish when no restaurant matched."""

    type: str = Field(
        description=(
            "Either restaurant or menu_item. When coordinates are provided and the name matches "
            "no restaurant, results may be menu items instead."
        )
    )
    id: str
    name: str
    is_chain: bool | None = None
    distance: float | None = Field(
        default=None,
        description=(
            "Distance from (latitude, longitude) in meters; present only when coordinates were "
            "provided."
        ),
    )
    city: str | None = None
    address1: str | None = None
    address2: str | None = None


class RestaurantSearchResponse(JanuaryModel):
    """Restaurants matching a name search."""

    total_count: int = Field(
        description="Total number of matches; may exceed the number of items returned."
    )
    items: list[RestaurantResult]


class MenuItem(JanuaryModel):
    """A dish from a restaurant menu, with its nutrition panel and servings."""

    type: str
    id: str
    name: str
    restaurant_name: str
    is_chain: bool | None = None
    nutrients: Nutrients | None = Field(
        default=None,
        description="Per-dish nutrition. Keys are omitted when the menu source has no value.",
    )
    glycemic_index: float | None = None
    glycemic_load: float | None = None
    image_url: str | None = None
    distance: float | None = Field(
        default=None, description="Distance from (latitude, longitude) in meters."
    )
    servings: list[FoodServing]


class MenuSearchResponse(JanuaryModel):
    """Menu items matching a dish search."""

    total_count: int = Field(
        description="Total number of matches; may exceed the number of items returned."
    )
    items: list[MenuItem]
