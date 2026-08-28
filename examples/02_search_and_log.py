"""Search the food database, pick a serving, and write it to an end user's food diary.

Demonstrates `foods.search`, `foods.get` (the only call that returns the complete list of servings),
`foods.lookup_barcode`, and the full food-log lifecycle: create, list, update, delete. Food-log
operations act on one person, so they require an end-user identifier - passed per call here, or set
once as `January(default_end_user_id=...)`.

Usage:
    export JANUARY_API_KEY=sk-...
    python examples/02_search_and_log.py "greek yogurt"

Cost: up to 7 credits, one per successful call - search, fetch, barcode lookup, create, list,
update, delete. Failed calls cost nothing.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

from january_ai import January, JanuaryError, NotFoundError

END_USER_ID = "demo-user-1042"

# A Coca-Cola can, from the API reference. Any 6 to 14 digit UPC-E, UPC-A, EAN-8, EAN-13 or GTIN-14.
EXAMPLE_UPC = "049000006346"


def main() -> int:
    """Search for a food, log a serving of it, then read, amend, and remove the log."""
    if os.environ.get("JANUARY_API_KEY") is None:
        print("set JANUARY_API_KEY to an sk- key from https://dashboard.january.ai")
        return 2

    query = sys.argv[1] if len(sys.argv) > 1 else "greek yogurt"

    with January() as client:
        try:
            return run(client, query)
        except JanuaryError as exc:
            print(f"request failed: {exc}")
            return 1


def run(client: January, query: str) -> int:
    """Do the work, with the client already open and errors handled by the caller."""
    # 1. Search. total_count is capped at 250, so 250 means "250 or more".
    results = client.foods.search(query, category="general", limit=5)
    print(f"{results.total_count} matches for {query!r}:")
    for candidate in results.items:
        brand = f"{candidate.brand_name} " if candidate.brand_name else ""
        print(f"  {candidate.id:>10}  {brand}{candidate.name}")

    if not results.items:
        print("nothing matched; try another query")
        return 1

    # 2. Fetch the food itself. Search and scan results carry one default serving; this is the call
    #    that returns every serving, which is what an end user picks between when logging.
    food = client.foods.get(results.items[0].id)
    print(f"\nservings for {food.name}:")
    for serving in food.servings:
        primary = " (default)" if serving.is_primary else ""
        print(f"  {serving.id:>10}  {serving.quantity:g} {serving.unit}{primary}")

    serving = next((option for option in food.servings if option.is_primary), food.servings[0])

    # 3. A barcode maps straight to a food, no ranking involved.
    try:
        scanned = client.foods.lookup_barcode(EXAMPLE_UPC)
        if scanned.items:
            print(f"\nbarcode {EXAMPLE_UPC} is {scanned.items[0].name}")
    except NotFoundError:
        print(f"\nbarcode {EXAMPLE_UPC} is not in the database")

    # 4. Log it. Food ids and serving ids come from a search, a fetch, or a scan detection.
    log = client.food_logs.create(
        [{"id": food.id, "serving": {"id": serving.id, "quantity": 1.5}}],
        name="Breakfast",
        timestamp_utc=datetime.now(timezone.utc),
        end_user_id=END_USER_ID,
        end_user_timezone="America/Los_Angeles",
    )
    print(f"\nlogged {log.id} at {log.timestamp_utc.isoformat()}")
    for logged in log.foods:
        calories = logged.nutrients.calories
        amount = f"{calories.value:g} {calories.unit}" if calories else "unknown calories"
        print(f"  {logged.name}: {logged.consumed_serving.quantity:g} serving(s), {amount}")

    # 5. List the diary. Both dates are inclusive UTC calendar days.
    today = date.today()
    logs = client.food_logs.list(today - timedelta(days=7), today, end_user_id=END_USER_ID)
    print(f"\n{logs.total_count} log(s) in the last week")

    # 6. Update only what is named; omitted arguments are left out of the request body entirely.
    renamed = client.food_logs.update(log.id, name="Second breakfast", end_user_id=END_USER_ID)
    print(f"renamed {renamed.id} to {renamed.name!r}")

    # 7. Delete. Idempotent: an unknown or already-deleted id returns the same response.
    deleted = client.food_logs.delete(log.id, end_user_id=END_USER_ID)
    print(f"delete reported {deleted.status!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
