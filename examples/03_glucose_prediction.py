"""Predict the glucose response to a meal, with no sensor involved.

Demonstrates `glucose.predict`: building a user profile, selecting foods by id and serving, and
reading the returned curve, impact score, and suggested chart bounds. Also shows the optional
personalization path, where CGM history and the meals eaten during it are sent together.

Usage:
    export JANUARY_API_KEY=sk-...
    python examples/03_glucose_prediction.py "white rice"

Cost: 3 credits - one search, one fetch, one prediction. Failed calls cost nothing.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from january_ai import January, JanuaryError
from january_ai.types import (
    CgmReadingParam,
    ConsumedFoodEntryParam,
    FoodSelectionParam,
    GlucoseUserProfileParam,
)

END_USER_ID = "demo-user-1042"

PROFILE: GlucoseUserProfileParam = {
    "age": 41,
    "sex": "male",
    "height": {"value": 70, "unit": "in"},
    "weight": {"value": 180, "unit": "lb"},
    "activity_level": "lightly_active",
    "health_conditions": ["prediabetes"],
}


def sparkline(values: list[float], low: float, high: float, width: int = 8) -> str:
    """Render a coarse ASCII bar for one glucose reading, scaled to the chart bounds."""
    span = high - low or 1.0
    filled = round(width * min(max((max(values) - low) / span, 0.0), 1.0))
    return "#" * filled + "." * (width - filled)


def main() -> int:
    """Predict the glucose curve for one serving of the food named on the command line."""
    if os.environ.get("JANUARY_API_KEY") is None:
        print("set JANUARY_API_KEY to an sk- key from https://dashboard.january.ai")
        return 2

    query = sys.argv[1] if len(sys.argv) > 1 else "white rice"

    with January(default_end_user_id=END_USER_ID) as client:
        try:
            results = client.foods.search(query, limit=1)
            if not results.items:
                print(f"nothing matched {query!r}")
                return 1

            # Search results carry one default serving; fetch the food for every serving it has.
            food = client.foods.get(results.items[0].id)
            serving = next((s for s in food.servings if s.is_primary), food.servings[0])
            meal: list[FoodSelectionParam] = [
                {"id": food.id, "serving": {"id": serving.id, "quantity": 1}}
            ]

            # start_time must carry a timezone: an aware datetime, or a string with a designator.
            eaten_at = datetime.now(timezone.utc)
            prediction = client.glucose.predict(
                user_profile=PROFILE,
                foods=meal,
                start_time=eaten_at,
                end_user_timezone="America/Los_Angeles",
            )
        except JanuaryError as exc:
            print(f"request failed: {exc}")
            return 1

    print(f"{food.name}, {serving.quantity:g} {serving.unit}")
    print(f"impact score: {prediction.impact_score}")
    print(f"chart bounds: {prediction.chart.min:g} to {prediction.chart.max:g} mg/dL\n")

    for point in prediction.prediction:
        bar = sparkline([point.value], prediction.chart.min, prediction.chart.max)
        print(f"  t+{point.minutes:>3} min  {point.value:6.1f} mg/dL  {bar}")

    peak = max(prediction.prediction, key=lambda point: point.value)
    print(f"\npeak of {peak.value:.1f} mg/dL at t+{peak.minutes} minutes")

    # Personalizing against real history is optional, but cgm_data and consumed_foods go together:
    # each requires the other, and the model wants at least five complete days of paired history.
    yesterday = eaten_at - timedelta(days=1)
    cgm_data: list[CgmReadingParam] = [{"timestamp": yesterday, "value": 94.0}]
    consumed_foods: list[ConsumedFoodEntryParam] = [
        {
            "timestamp": yesterday + timedelta(minutes=5),
            "id": food.id,
            "serving": {"id": serving.id, "quantity": 1},
        }
    ]
    print(f"\n(personalization would send {len(cgm_data)} reading(s), {len(consumed_foods)} meal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
