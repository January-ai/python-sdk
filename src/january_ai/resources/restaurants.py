"""Searching nearby restaurants and the dishes on their menus.

All distances, given and returned, are in meters. The client-token scope both operations need is
recorded on the resource classes themselves.
"""

from __future__ import annotations

from typing import cast

import httpx

from .._base_client import AsyncAPIClient, SyncAPIClient
from .._constants import API_VERSION_PATH
from .._types import NOT_GIVEN, NotGiven, RequestSpec
from ..types import MenuSearchResponse, RestaurantSearchResponse

__all__ = ["AsyncRestaurants", "Restaurants"]

_RESTAURANTS_PATH = f"{API_VERSION_PATH}/restaurants"


def _search_restaurants_spec(
    query: str,
    *,
    latitude: float,
    longitude: float,
    radius: int | None,
    limit: int | None,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[RestaurantSearchResponse]:
    """Describe the request that searches restaurants around a location."""
    return RequestSpec(
        method="GET",
        path=_RESTAURANTS_PATH,
        cast_to=RestaurantSearchResponse,
        params={
            "query": query,
            "latitude": latitude,
            "longitude": longitude,
            "radius": radius,
            "limit": limit,
        },
        end_user_id=end_user_id,
        timeout=timeout,
    )


def _search_menu_items_spec(
    query: str,
    *,
    latitude: float,
    longitude: float,
    radius: int | None,
    limit: int | None,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[MenuSearchResponse]:
    """Describe the request that searches menu items around a location."""
    return RequestSpec(
        method="GET",
        path=f"{_RESTAURANTS_PATH}/menu-items",
        cast_to=MenuSearchResponse,
        params={
            "query": query,
            "latitude": latitude,
            "longitude": longitude,
            "radius": radius,
            "limit": limit,
        },
        end_user_id=end_user_id,
        timeout=timeout,
    )


class Restaurants:
    """Find restaurants near an end user and the dishes they serve.

    Reached as ``client.restaurants``. With a client token, both operations need the
    ``restaurants:read`` scope. Both searches are anchored to a coordinate pair and ranked by
    proximity, and both report each result's ``distance`` in meters.

    Example:
        Offer the nearby options for what a user typed::

            here = (37.7749, -122.4194)
            places = client.restaurants.search(
                "sweetgreen", latitude=here[0], longitude=here[1], radius=5000
            )
            dishes = client.restaurants.search_menu_items(
                "burger", latitude=here[0], longitude=here[1], limit=20
            )
    """

    def __init__(self, client: SyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    def search(
        self,
        query: str,
        *,
        latitude: float,
        longitude: float,
        radius: int | None = None,
        limit: int | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> RestaurantSearchResponse:
        """Search restaurants by name around a location, ranked by proximity.

        Args:
            query: Restaurant name to search for, at most 256 characters.
            latitude: Latitude of the search location, between -90 and 90.
            longitude: Longitude of the search location, between -180 and 180.
            radius: Search radius in meters, between 1 and 50000 (about 31 miles). Defaults to
                8000 meters, about 5 miles, server-side.
            limit: Maximum number of results, between 1 and 100. Defaults to 10 server-side.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The matches and a ``total_count`` that may exceed them. When the name matches no
            restaurant, results may be dishes instead: check each item's ``type``, which is
            either ``restaurant`` or ``menu_item``.

        Raises:
            BadRequestError: If a parameter is missing or invalid; the message names it and the
                accepted values.
        """
        return cast(
            RestaurantSearchResponse,
            self._client.send(
                _search_restaurants_spec(
                    query,
                    latitude=latitude,
                    longitude=longitude,
                    radius=radius,
                    limit=limit,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )

    def search_menu_items(
        self,
        query: str,
        *,
        latitude: float,
        longitude: float,
        radius: int | None = None,
        limit: int | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> MenuSearchResponse:
        """Search dishes across restaurants near a location.

        Args:
            query: Dish or restaurant name to search for, at most 256 characters.
            latitude: Latitude of the search location, between -90 and 90.
            longitude: Longitude of the search location, between -180 and 180.
            radius: Search radius in meters, between 1 and 50000 (about 31 miles). Defaults to
                8000 meters, about 5 miles, server-side.
            limit: Maximum number of results, between 1 and 100. Defaults to 10 server-side.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The matching menu items with their nutrition values and servings, and a
            ``total_count`` that may exceed the items returned.

        Raises:
            BadRequestError: If a parameter is missing or invalid; the message names it and the
                accepted values.
        """
        return cast(
            MenuSearchResponse,
            self._client.send(
                _search_menu_items_spec(
                    query,
                    latitude=latitude,
                    longitude=longitude,
                    radius=radius,
                    limit=limit,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )


class AsyncRestaurants:
    """Find restaurants and dishes near an end user, without blocking the event loop.

    Reached as ``client.restaurants`` on :class:`~january_ai.AsyncJanuary`. Identical to
    :class:`Restaurants` in arguments and results, including needing the ``restaurants:read``
    scope on a client token.

    Example:
        Run both searches concurrently for one screen::

            places, dishes = await asyncio.gather(
                client.restaurants.search("sweetgreen", latitude=37.7749, longitude=-122.4194),
                client.restaurants.search_menu_items(
                    "burger", latitude=37.7749, longitude=-122.4194
                ),
            )
    """

    def __init__(self, client: AsyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    async def search(
        self,
        query: str,
        *,
        latitude: float,
        longitude: float,
        radius: int | None = None,
        limit: int | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> RestaurantSearchResponse:
        """Search restaurants by name around a location, ranked by proximity.

        Args:
            query: Restaurant name to search for, at most 256 characters.
            latitude: Latitude of the search location, between -90 and 90.
            longitude: Longitude of the search location, between -180 and 180.
            radius: Search radius in meters, between 1 and 50000 (about 31 miles). Defaults to
                8000 meters, about 5 miles, server-side.
            limit: Maximum number of results, between 1 and 100. Defaults to 10 server-side.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The matches and a ``total_count`` that may exceed them. When the name matches no
            restaurant, results may be dishes instead: check each item's ``type``, which is
            either ``restaurant`` or ``menu_item``.

        Raises:
            BadRequestError: If a parameter is missing or invalid; the message names it and the
                accepted values.
        """
        return cast(
            RestaurantSearchResponse,
            await self._client.send(
                _search_restaurants_spec(
                    query,
                    latitude=latitude,
                    longitude=longitude,
                    radius=radius,
                    limit=limit,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )

    async def search_menu_items(
        self,
        query: str,
        *,
        latitude: float,
        longitude: float,
        radius: int | None = None,
        limit: int | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> MenuSearchResponse:
        """Search dishes across restaurants near a location.

        Args:
            query: Dish or restaurant name to search for, at most 256 characters.
            latitude: Latitude of the search location, between -90 and 90.
            longitude: Longitude of the search location, between -180 and 180.
            radius: Search radius in meters, between 1 and 50000 (about 31 miles). Defaults to
                8000 meters, about 5 miles, server-side.
            limit: Maximum number of results, between 1 and 100. Defaults to 10 server-side.
            end_user_id: Your identifier for the end user this call acts on behalf of. Omit to
                use the client's default; pass ``None`` to send no identifier at all.
            timeout: A timeout for this call only, overriding the client's.

        Returns:
            The matching menu items with their nutrition values and servings, and a
            ``total_count`` that may exceed the items returned.

        Raises:
            BadRequestError: If a parameter is missing or invalid; the message names it and the
                accepted values.
        """
        return cast(
            MenuSearchResponse,
            await self._client.send(
                _search_menu_items_spec(
                    query,
                    latitude=latitude,
                    longitude=longitude,
                    radius=radius,
                    limit=limit,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )
