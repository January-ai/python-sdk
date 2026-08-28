"""Nearby restaurant search and menu-item search.

Both operations are anchored to a coordinate pair, so the interesting part is how the floats and
the optional integers reach the query string.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from january_ai import AsyncJanuary, January
from january_ai.types import MenuSearchResponse, RestaurantSearchResponse

BASE_URL = "https://partners.january.ai"
RESTAURANTS_URL = f"{BASE_URL}/v1.2/restaurants"
MENU_ITEMS_URL = f"{RESTAURANTS_URL}/menu-items"

RESTAURANTS_PAYLOAD = {
    "total_count": 12,
    "items": [
        {
            "type": "restaurant",
            "id": "53fc3b8a-e6bf-404d-83c8-9f42124d1bee",
            "name": "McDonald's",
            "is_chain": False,
            "distance": 124,
            "city": "San Francisco",
            "address1": "123 Main Street",
            "address2": "Suite 100",
        }
    ],
}

MENU_ITEMS_PAYLOAD = {
    "total_count": 7,
    "items": [
        {
            "type": "menu_item",
            "id": "228990954",
            "name": "burger",
            "restaurant_name": "morning due cafe",
            "is_chain": False,
            "nutrients": {"calories": {"value": 540, "unit": "kcal"}},
            "distance": 124,
            "servings": [
                {
                    "id": 68051535,
                    "quantity": 1,
                    "unit": "oz",
                    "scaling_factor": 1,
                    "weight_grams": 28,
                    "is_primary": True,
                }
            ],
        }
    ],
}


def _fingerprint(request: httpx.Request) -> tuple[str, str, bytes]:
    """Reduce a request to the parts the synchronous and asynchronous clients must agree on."""
    return (request.method, str(request.url), request.content)


@pytest.mark.anyio
async def test_search_serializes_the_location_and_the_limits(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Send the coordinates as unquoted decimals and parse the ranked matches back."""
    route = respx_mock.get(RESTAURANTS_URL).mock(
        return_value=httpx.Response(200, json=RESTAURANTS_PAYLOAD)
    )

    places = client.restaurants.search(
        "sweetgreen", latitude=37.7749, longitude=-122.4194, radius=5000, limit=20
    )

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v1.2/restaurants"
    assert dict(request.url.params) == {
        "query": "sweetgreen",
        "latitude": "37.7749",
        "longitude": "-122.4194",
        "radius": "5000",
        "limit": "20",
    }
    assert request.content == b""

    assert isinstance(places, RestaurantSearchResponse)
    assert places.total_count == 12
    assert places.items[0].type == "restaurant"
    assert places.items[0].name == "McDonald's"
    assert places.items[0].distance == 124.0
    assert places.items[0].city == "San Francisco"

    await async_client.restaurants.search(
        "sweetgreen", latitude=37.7749, longitude=-122.4194, radius=5000, limit=20
    )
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_search_drops_the_unset_radius_and_limit(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Leave radius and limit out entirely so the server's defaults apply."""
    route = respx_mock.get(RESTAURANTS_URL).mock(
        return_value=httpx.Response(200, json=RESTAURANTS_PAYLOAD)
    )

    client.restaurants.search("sweetgreen", latitude=37.7749, longitude=-122.4194)

    assert dict(route.calls.last.request.url.params) == {
        "query": "sweetgreen",
        "latitude": "37.7749",
        "longitude": "-122.4194",
    }


@pytest.mark.anyio
async def test_search_menu_items_hits_its_own_path(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Search dishes at ``/restaurants/menu-items`` and parse their nutrition and servings."""
    route = respx_mock.get(MENU_ITEMS_URL).mock(
        return_value=httpx.Response(200, json=MENU_ITEMS_PAYLOAD)
    )

    dishes = client.restaurants.search_menu_items(
        "burger", latitude=37.7749, longitude=-122.4194, radius=5000, limit=20
    )

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v1.2/restaurants/menu-items"
    assert dict(request.url.params) == {
        "query": "burger",
        "latitude": "37.7749",
        "longitude": "-122.4194",
        "radius": "5000",
        "limit": "20",
    }

    assert isinstance(dishes, MenuSearchResponse)
    assert dishes.total_count == 7
    assert dishes.items[0].id == "228990954"
    assert dishes.items[0].restaurant_name == "morning due cafe"
    nutrients = dishes.items[0].nutrients
    assert nutrients is not None
    assert nutrients.calories is not None
    assert nutrients.calories.value == 540.0
    assert dishes.items[0].servings[0].scaling_factor == 1.0

    await async_client.restaurants.search_menu_items(
        "burger", latitude=37.7749, longitude=-122.4194, radius=5000, limit=20
    )
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_search_menu_items_sends_the_end_user_header_when_named(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Attribute a menu search to an end user when one is passed."""
    route = respx_mock.get(MENU_ITEMS_URL).mock(
        return_value=httpx.Response(200, json=MENU_ITEMS_PAYLOAD)
    )

    client.restaurants.search_menu_items(
        "burger", latitude=37.7749, longitude=-122.4194, end_user_id="acme-user-8271"
    )

    assert route.calls.last.request.headers["x-end-user-id"] == "acme-user-8271"
