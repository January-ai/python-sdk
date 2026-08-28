"""Food search, autocomplete, lookup by id and barcode, and healthier alternatives."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from january_ai import AsyncJanuary, January
from january_ai.types import (
    Food,
    FoodAlternativesResponse,
    FoodSearchResponse,
    FoodSuggestionsResponse,
)

BASE_URL = "https://partners.january.ai"
FOODS_URL = f"{BASE_URL}/v1.2/foods"
AUTOCOMPLETE_URL = f"{FOODS_URL}/autocomplete"
FOOD_URL = f"{FOODS_URL}/101963552"
BARCODE_URL = f"{FOODS_URL}/barcode/049000006346"
ALTERNATIVES_URL = f"{FOODS_URL}/70372230/alternatives"

FOOD_PAYLOAD = {
    "id": 101963552,
    "name": "Dipped Banana Bites",
    "brand_name": "Banana",
    "nutrients": {
        "calories": {"value": 300, "unit": "kcal"},
        "protein": {"value": 4.5, "unit": "g"},
    },
    "glycemic_index": 11.3,
    "glycemic_load": 1.4,
    "upc": "049000006346",
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
SEARCH_PAYLOAD = {"total_count": 132, "items": [FOOD_PAYLOAD]}

SUGGESTIONS_PAYLOAD = {
    "items": [
        {
            "id": 70376053,
            "name": "greek yogurt",
            "brand_name": "Chobani",
            "nutrients": {"calories": {"value": 100, "unit": "kcal"}},
        }
    ]
}

ALTERNATIVES_PAYLOAD = {
    "alternatives": [
        {
            "food": {
                "id": 70379835,
                "name": "Oatmeal",
                "brand_name": "",
                "nutrients": {"calories": {"value": 150, "unit": "kcal"}},
                "servings": [{"id": 34237662, "quantity": 1, "unit": "cup"}],
            }
        }
    ]
}


def _fingerprint(request: httpx.Request) -> tuple[str, str, bytes]:
    """Reduce a request to the parts the synchronous and asynchronous clients must agree on."""
    return (request.method, str(request.url), request.content)


@pytest.mark.anyio
async def test_search_serializes_every_query_parameter(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Send query, category, and limit as a query string, and parse the page back."""
    route = respx_mock.get(FOODS_URL).mock(return_value=httpx.Response(200, json=SEARCH_PAYLOAD))

    results = client.foods.search("greek yogurt", category="branded", limit=5)

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v1.2/foods"
    assert dict(request.url.params) == {
        "query": "greek yogurt",
        "category": "branded",
        "limit": "5",
    }
    assert request.content == b""

    assert isinstance(results, FoodSearchResponse)
    assert results.total_count == 132
    assert results.items[0].id == 101963552
    assert results.items[0].name == "Dipped Banana Bites"
    calories = results.items[0].nutrients.calories
    assert calories is not None
    assert calories.value == 300.0
    assert calories.unit == "kcal"

    await async_client.foods.search("greek yogurt", category="branded", limit=5)
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_search_drops_unset_parameters(client: January, respx_mock: respx.MockRouter) -> None:
    """Leave category and limit out of the query so the API's own defaults apply."""
    route = respx_mock.get(FOODS_URL).mock(return_value=httpx.Response(200, json=SEARCH_PAYLOAD))

    client.foods.search("greek yogurt")

    assert dict(route.calls.last.request.url.params) == {"query": "greek yogurt"}


@pytest.mark.anyio
async def test_autocomplete_hits_its_own_path(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Suggest names from ``/foods/autocomplete``, which is a different endpoint from search."""
    route = respx_mock.get(AUTOCOMPLETE_URL).mock(
        return_value=httpx.Response(200, json=SUGGESTIONS_PAYLOAD)
    )

    suggestions = client.foods.autocomplete("ban", category="general", limit=3)

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v1.2/foods/autocomplete"
    assert dict(request.url.params) == {"query": "ban", "category": "general", "limit": "3"}

    assert isinstance(suggestions, FoodSuggestionsResponse)
    assert suggestions.items[0].id == 70376053
    assert suggestions.items[0].name == "greek yogurt"
    assert suggestions.items[0].brand_name == "Chobani"

    await async_client.foods.autocomplete("ban", category="general", limit=3)
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


@pytest.mark.anyio
async def test_get_accepts_an_integer_food_id(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Accept the ``int`` id the models carry and build ``/v1.2/foods/101963552`` from it."""
    route = respx_mock.get(FOOD_URL).mock(return_value=httpx.Response(200, json=FOOD_PAYLOAD))

    food = client.foods.get(101963552)

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v1.2/foods/101963552"
    assert not request.url.params
    assert request.content == b""

    assert isinstance(food, Food)
    assert food.id == 101963552
    assert food.upc == "049000006346"
    assert food.servings[0].id == 68051535
    assert food.servings[0].unit == "oz"
    assert food.servings[0].weight_grams == 28.0
    assert food.servings[0].is_primary is True

    await async_client.foods.get(101963552)
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_get_accepts_the_string_form_of_a_food_id(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Produce the same path from the string form of an id as from the integer."""
    route = respx_mock.get(FOOD_URL).mock(return_value=httpx.Response(200, json=FOOD_PAYLOAD))

    client.foods.get("101963552")

    assert route.calls.last.request.url.path == "/v1.2/foods/101963552"


@pytest.mark.anyio
async def test_lookup_barcode_puts_the_upc_in_the_path(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Look a barcode up by path segment and return it in the search envelope."""
    route = respx_mock.get(BARCODE_URL).mock(
        return_value=httpx.Response(200, json={"total_count": 1, "items": [FOOD_PAYLOAD]})
    )

    match = client.foods.lookup_barcode("049000006346")

    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.path == "/v1.2/foods/barcode/049000006346"
    assert not request.url.params

    assert isinstance(match, FoodSearchResponse)
    assert match.total_count == 1
    assert match.items[0].upc == "049000006346"

    await async_client.foods.lookup_barcode("049000006346")
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


@pytest.mark.anyio
async def test_suggest_alternatives_sends_the_diet_arrays(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """POST the restrictions and preferences that constrain which alternatives come back."""
    route = respx_mock.post(ALTERNATIVES_URL).mock(
        return_value=httpx.Response(200, json=ALTERNATIVES_PAYLOAD)
    )

    alternatives = client.foods.suggest_alternatives(
        70372230, diet_restrictions=["gluten", "dairy"], diet_preferences=["vegetarian"]
    )

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path == "/v1.2/foods/70372230/alternatives"
    assert json.loads(request.content) == {
        "diet_restrictions": ["gluten", "dairy"],
        "diet_preferences": ["vegetarian"],
    }

    assert isinstance(alternatives, FoodAlternativesResponse)
    assert alternatives.alternatives[0].food.name == "Oatmeal"
    assert alternatives.alternatives[0].food.id == 70379835
    servings = alternatives.alternatives[0].food.servings
    assert servings is not None
    assert servings[0].unit == "cup"

    await async_client.foods.suggest_alternatives(
        70372230, diet_restrictions=["gluten", "dairy"], diet_preferences=["vegetarian"]
    )
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_suggest_alternatives_sends_an_empty_object_when_no_diet_is_given(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Send ``{}`` rather than no body at all, which is what the endpoint requires."""
    route = respx_mock.post(ALTERNATIVES_URL).mock(
        return_value=httpx.Response(200, json=ALTERNATIVES_PAYLOAD)
    )

    client.foods.suggest_alternatives(70372230)

    request = route.calls.last.request
    assert request.content == b"{}"
    assert json.loads(request.content) == {}


def test_suggest_alternatives_sends_empty_arrays_as_given(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Distinguish an explicit empty sequence, which is sent, from an omitted one, which is not."""
    route = respx_mock.post(ALTERNATIVES_URL).mock(
        return_value=httpx.Response(200, json=ALTERNATIVES_PAYLOAD)
    )

    client.foods.suggest_alternatives(70372230, diet_restrictions=[])

    assert json.loads(route.calls.last.request.content) == {"diet_restrictions": []}
