"""Searching the food database, looking foods up, and finding healthier alternatives.

These are the calls a mobile app makes directly, so the scope they need is recorded on the resource
classes themselves rather than only here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast
from urllib.parse import quote

import httpx

from .._base_client import AsyncAPIClient, SyncAPIClient
from .._constants import API_VERSION_PATH
from .._types import NOT_GIVEN, NotGiven, RequestSpec
from ..types import (
    AutocompleteCategory,
    DietPreference,
    DietRestriction,
    Food,
    FoodAlternativesResponse,
    FoodCategory,
    FoodSearchResponse,
    FoodSuggestionsResponse,
)

__all__ = ["AsyncFoods", "Foods"]

_FOODS_PATH = f"{API_VERSION_PATH}/foods"


def _food_path(food_id: int | str, *suffix: str) -> str:
    """Build a path under ``/foods`` with the id escaped as a single segment.

    The API constrains ``food_id`` to digits, but percent-encoding it here means an id that
    reached the caller from somewhere else cannot smuggle in a slash and address a different
    endpoint.
    """
    segment = quote(str(food_id), safe="")
    return "/".join((_FOODS_PATH, segment, *suffix))


def _search_foods_spec(
    query: str,
    *,
    category: FoodCategory | None,
    limit: int | None,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[FoodSearchResponse]:
    """Describe the request that searches foods by name."""
    return RequestSpec(
        method="GET",
        path=_FOODS_PATH,
        cast_to=FoodSearchResponse,
        params={"query": query, "category": category, "limit": limit},
        end_user_id=end_user_id,
        timeout=timeout,
    )


def _autocomplete_foods_spec(
    query: str,
    *,
    category: AutocompleteCategory | None,
    limit: int | None,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[FoodSuggestionsResponse]:
    """Describe the request that suggests food names for a partial query."""
    return RequestSpec(
        method="GET",
        path=f"{_FOODS_PATH}/autocomplete",
        cast_to=FoodSuggestionsResponse,
        params={"query": query, "category": category, "limit": limit},
        end_user_id=end_user_id,
        timeout=timeout,
    )


def _get_food_spec(
    food_id: int | str,
    *,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[Food]:
    """Describe the request that fetches one food's full record."""
    return RequestSpec(
        method="GET",
        path=_food_path(food_id),
        cast_to=Food,
        end_user_id=end_user_id,
        timeout=timeout,
    )


def _lookup_barcode_spec(
    upc: str,
    *,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[FoodSearchResponse]:
    """Describe the request that looks a food up by barcode."""
    return RequestSpec(
        method="GET",
        path=f"{_FOODS_PATH}/barcode/{quote(upc, safe='')}",
        cast_to=FoodSearchResponse,
        end_user_id=end_user_id,
        timeout=timeout,
    )


def _suggest_alternatives_spec(
    food_id: int | str,
    *,
    diet_restrictions: Sequence[DietRestriction] | None,
    diet_preferences: Sequence[DietPreference] | None,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[FoodAlternativesResponse]:
    """Describe the request that asks for healthier alternatives to a food."""
    body: dict[str, object] = {}
    if diet_restrictions is not None:
        body["diet_restrictions"] = list(diet_restrictions)
    if diet_preferences is not None:
        body["diet_preferences"] = list(diet_preferences)
    return RequestSpec(
        method="POST",
        path=_food_path(food_id, "alternatives"),
        cast_to=FoodAlternativesResponse,
        # The endpoint expects a JSON body even when neither array applies, so an empty object is
        # sent rather than no body at all.
        json_body=body,
        end_user_id=end_user_id,
        timeout=timeout,
    )


class Foods:
    """Search the January food database and read what it knows about a food.

    Reached as ``client.foods``. With a client token, every operation here needs the
    ``foods:read`` scope. Search, barcode, and scan results carry a single default
    serving; :meth:`get` is what returns the complete list, which is what lets an end user pick
    "1 cup" against "100 g" before logging.

    Example:
        Take a user from a few typed characters to a logged portion::

            suggestions = client.foods.autocomplete("gree")
            food = client.foods.get(suggestions.items[0].id)
            serving = next(s for s in food.servings if s.is_primary)
            client.food_logs.create(
                [{"id": food.id, "serving": {"id": serving.id, "quantity": 1}}],
                end_user_id="acme-user-8271",
            )
    """

    def __init__(self, client: SyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    def search(
        self,
        query: str,
        *,
        category: FoodCategory | None = None,
        limit: int | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodSearchResponse:
        """Search foods by name.

        Full-text search over the January food database, returning up to 40 ranked matches. To
        look a scanned barcode up, use :meth:`lookup_barcode` instead.

        Args:
            query: The food name to search for, at most 256 characters.
            category: Narrows results to one category. Defaults to ``general`` server-side.
            limit: Maximum number of results, between 1 and 40. Defaults to 10 server-side.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The matching foods and a ``total_count`` that may exceed them. The count is capped at
            250, so 250 means "250 or more". There is no offset or cursor: raise ``limit`` (up to
            40) or narrow the query, since results past the limit cannot be retrieved.

        Raises:
            BadRequestError: If a parameter is missing or invalid; the message names it.
        """
        return cast(
            FoodSearchResponse,
            self._client.send(
                _search_foods_spec(
                    query,
                    category=category,
                    limit=limit,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )

    def autocomplete(
        self,
        query: str,
        *,
        category: AutocompleteCategory | None = None,
        limit: int | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodSuggestionsResponse:
        """Suggest food names for the characters a user has typed so far.

        Built for type-ahead: "ban" yields banana, banana bread, and so on, generic foods before
        branded, each with its id, name, brand, a thumbnail, and calories. Once the user picks
        one, call :meth:`get` for its servings and full nutrition.

        Args:
            query: The characters typed so far, at most 64. Fewer than two letters or digits
                yield no suggestions.
            category: Narrows suggestions to one category. Omitted, generic and branded foods are
                suggested together, generic first. Note that ``recipe`` is not offered here, only
                on :meth:`search`.
            limit: Maximum number of suggestions, between 1 and 20. Defaults to 8 server-side.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The ranked suggestions. ``items`` is empty for a query that is too short, for no
            match, and for a search-index error, since the suggestion service fails open rather
            than interrupt a typing user. An unreachable service still answers 502 or 504.
        """
        return cast(
            FoodSuggestionsResponse,
            self._client.send(
                _autocomplete_foods_spec(
                    query,
                    category=category,
                    limit=limit,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )

    def get(
        self,
        food_id: int | str,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> Food:
        """Get one food's full record, most importantly every serving size it supports.

        Nutrition is reported per the food's default serving, in the nutrient vocabulary shared
        across the API.

        Args:
            food_id: Numeric food id from a search, scan, or detection result, as an ``int`` or
                the string form of one.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The food, its nutrient panel, and all of its servings.

        Raises:
            NotFoundError: If no food has this id.
            BadRequestError: If ``food_id`` is not a numeric id.
        """
        return cast(
            Food,
            self._client.send(_get_food_spec(food_id, end_user_id=end_user_id, timeout=timeout)),
        )

    def lookup_barcode(
        self,
        upc: str,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodSearchResponse:
        """Look a food up by its barcode.

        An exact lookup rather than a search: for free text use :meth:`search`. The match carries
        one default serving, so fetch the food with :meth:`get` before offering portion choices.

        Args:
            upc: The numeric barcode, 6 to 14 digits (UPC-E, UPC-A, EAN-8, EAN-13, or GTIN-14).
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The matching food in the same envelope :meth:`search` returns, with the food in
            ``items``.

        Raises:
            NotFoundError: If no food matches this barcode.
            BadRequestError: If the barcode is not a 6 to 14 digit number.
        """
        return cast(
            FoodSearchResponse,
            self._client.send(_lookup_barcode_spec(upc, end_user_id=end_user_id, timeout=timeout)),
        )

    def suggest_alternatives(
        self,
        food_id: int | str,
        *,
        diet_restrictions: Sequence[DietRestriction] | None = None,
        diet_preferences: Sequence[DietPreference] | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodAlternativesResponse:
        """Suggest healthier alternatives to a food, honoring dietary rules.

        Args:
            food_id: Numeric food id from a search, scan, or detection result, as an ``int`` or
                the string form of one.
            diet_restrictions: Allergens or ingredients to avoid. Omit, or pass an empty
                sequence, if none apply.
            diet_preferences: Dietary patterns to match. Omit, or pass an empty sequence, if none
                apply.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The alternatives, each with the food's macro nutrients and servings. An empty
            ``alternatives`` list is a valid result meaning nothing suitable was found, not an
            error.

        Raises:
            NotFoundError: If no food has this id.
            BadRequestError: If a value falls outside the allowed vocabulary; the message names
                it.
        """
        return cast(
            FoodAlternativesResponse,
            self._client.send(
                _suggest_alternatives_spec(
                    food_id,
                    diet_restrictions=diet_restrictions,
                    diet_preferences=diet_preferences,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )


class AsyncFoods:
    """Search the January food database, without blocking the event loop.

    Reached as ``client.foods`` on :class:`~january_ai.AsyncJanuary`. Identical to :class:`Foods`
    in arguments and results, including needing the ``foods:read`` scope on a client token.

    Example:
        Fan several lookups out at once::

            foods = await asyncio.gather(
                *(client.foods.get(food_id) for food_id in picked_ids)
            )
    """

    def __init__(self, client: AsyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    async def search(
        self,
        query: str,
        *,
        category: FoodCategory | None = None,
        limit: int | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodSearchResponse:
        """Search foods by name.

        Full-text search over the January food database, returning up to 40 ranked matches. To
        look a scanned barcode up, use :meth:`lookup_barcode` instead.

        Args:
            query: The food name to search for, at most 256 characters.
            category: Narrows results to one category. Defaults to ``general`` server-side.
            limit: Maximum number of results, between 1 and 40. Defaults to 10 server-side.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The matching foods and a ``total_count`` that may exceed them. The count is capped at
            250, so 250 means "250 or more". There is no offset or cursor: raise ``limit`` (up to
            40) or narrow the query, since results past the limit cannot be retrieved.

        Raises:
            BadRequestError: If a parameter is missing or invalid; the message names it.
        """
        return cast(
            FoodSearchResponse,
            await self._client.send(
                _search_foods_spec(
                    query,
                    category=category,
                    limit=limit,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )

    async def autocomplete(
        self,
        query: str,
        *,
        category: AutocompleteCategory | None = None,
        limit: int | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodSuggestionsResponse:
        """Suggest food names for the characters a user has typed so far.

        Built for type-ahead: "ban" yields banana, banana bread, and so on, generic foods before
        branded, each with its id, name, brand, a thumbnail, and calories. Once the user picks
        one, call :meth:`get` for its servings and full nutrition.

        Args:
            query: The characters typed so far, at most 64. Fewer than two letters or digits
                yield no suggestions.
            category: Narrows suggestions to one category. Omitted, generic and branded foods are
                suggested together, generic first. Note that ``recipe`` is not offered here, only
                on :meth:`search`.
            limit: Maximum number of suggestions, between 1 and 20. Defaults to 8 server-side.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The ranked suggestions. ``items`` is empty for a query that is too short, for no
            match, and for a search-index error, since the suggestion service fails open rather
            than interrupt a typing user. An unreachable service still answers 502 or 504.
        """
        return cast(
            FoodSuggestionsResponse,
            await self._client.send(
                _autocomplete_foods_spec(
                    query,
                    category=category,
                    limit=limit,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )

    async def get(
        self,
        food_id: int | str,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> Food:
        """Get one food's full record, most importantly every serving size it supports.

        Nutrition is reported per the food's default serving, in the nutrient vocabulary shared
        across the API.

        Args:
            food_id: Numeric food id from a search, scan, or detection result, as an ``int`` or
                the string form of one.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The food, its nutrient panel, and all of its servings.

        Raises:
            NotFoundError: If no food has this id.
            BadRequestError: If ``food_id`` is not a numeric id.
        """
        return cast(
            Food,
            await self._client.send(
                _get_food_spec(food_id, end_user_id=end_user_id, timeout=timeout)
            ),
        )

    async def lookup_barcode(
        self,
        upc: str,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodSearchResponse:
        """Look a food up by its barcode.

        An exact lookup rather than a search: for free text use :meth:`search`. The match carries
        one default serving, so fetch the food with :meth:`get` before offering portion choices.

        Args:
            upc: The numeric barcode, 6 to 14 digits (UPC-E, UPC-A, EAN-8, EAN-13, or GTIN-14).
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The matching food in the same envelope :meth:`search` returns, with the food in
            ``items``.

        Raises:
            NotFoundError: If no food matches this barcode.
            BadRequestError: If the barcode is not a 6 to 14 digit number.
        """
        return cast(
            FoodSearchResponse,
            await self._client.send(
                _lookup_barcode_spec(upc, end_user_id=end_user_id, timeout=timeout)
            ),
        )

    async def suggest_alternatives(
        self,
        food_id: int | str,
        *,
        diet_restrictions: Sequence[DietRestriction] | None = None,
        diet_preferences: Sequence[DietPreference] | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> FoodAlternativesResponse:
        """Suggest healthier alternatives to a food, honoring dietary rules.

        Args:
            food_id: Numeric food id from a search, scan, or detection result, as an ``int`` or
                the string form of one.
            diet_restrictions: Allergens or ingredients to avoid. Omit, or pass an empty
                sequence, if none apply.
            diet_preferences: Dietary patterns to match. Omit, or pass an empty sequence, if none
                apply.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The alternatives, each with the food's macro nutrients and servings. An empty
            ``alternatives`` list is a valid result meaning nothing suitable was found, not an
            error.

        Raises:
            NotFoundError: If no food has this id.
            BadRequestError: If a value falls outside the allowed vocabulary; the message names
                it.
        """
        return cast(
            FoodAlternativesResponse,
            await self._client.send(
                _suggest_alternatives_spec(
                    food_id,
                    diet_restrictions=diet_restrictions,
                    diet_preferences=diet_preferences,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )
