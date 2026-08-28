"""The seven resource groups that make up the January AI API surface.

Each group comes as a pair: a blocking class reached from :class:`~january_ai.January` and an
``Async``-prefixed one reached from :class:`~january_ai.AsyncJanuary`. The two are deliberately
identical in method names, parameters, and results, so porting code between them is a matter of
adding ``await``. Resources are constructed by the client and reached as attributes on it -
``client.foods.search(...)`` - rather than instantiated directly.
"""

from __future__ import annotations

from .auth import AsyncAuth, Auth
from .credits import AsyncCredits, Credits
from .food_logs import AsyncFoodLogs, FoodLogs
from .food_scans import AsyncFoodScans, FoodScans
from .foods import AsyncFoods, Foods
from .glucose import AsyncGlucose, Glucose
from .restaurants import AsyncRestaurants, Restaurants

__all__ = [
    "AsyncAuth",
    "AsyncCredits",
    "AsyncFoodLogs",
    "AsyncFoodScans",
    "AsyncFoods",
    "AsyncGlucose",
    "AsyncRestaurants",
    "Auth",
    "Credits",
    "FoodLogs",
    "FoodScans",
    "Foods",
    "Glucose",
    "Restaurants",
]
