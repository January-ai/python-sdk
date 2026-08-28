# january-ai

[![PyPI version](https://img.shields.io/pypi/v/january-ai.svg)](https://pypi.org/project/january-ai/)
[![CI](https://img.shields.io/github/actions/workflow/status/januaryai/python-sdk/ci.yml?branch=main&label=CI)](https://github.com/januaryai/python-sdk/actions/workflows/ci.yml)
[![Python versions](https://img.shields.io/pypi/pyversions/january-ai.svg)](https://pypi.org/project/january-ai/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/januaryai/python-sdk/blob/main/LICENSE)

The official Python SDK for the [January AI](https://january.ai) nutrition intelligence API: one
API for understanding what people eat and how food may affect them. It ships a blocking client and
an identical async one, fully typed, with retries, timeouts, and image preparation handled for you.

- **Scan a meal** from a photo or a sentence, then correct the result in plain English.
- **Search foods** by name or barcode, and read every serving size a food supports.
- **Suggest healthier alternatives**, filtered by allergens and dietary patterns.
- **Keep a per-user food diary**: create, list by date range, update, delete.
- **Find restaurants and dishes** near a location, with nutrition.
- **Predict a glucose response** to a meal, with no sensor involved.

| | |
| --- | --- |
| Documentation | [docs.january.ai](https://docs.january.ai) |
| API reference | [docs.january.ai/rest-api](https://docs.january.ai/rest-api) |
| Developer Dashboard | [dashboard.january.ai](https://dashboard.january.ai) |
| Support | [support@january.ai](mailto:support@january.ai) |
| Community | [Discord](https://discord.gg/cYQeh3UnC) |
| API version this SDK targets | `/v1.2` |

## Contents

- [Getting an API key](#getting-an-api-key)
- [Installation](#installation)
- [Quickstart](#quickstart)
- [All 18 operations at a glance](#all-18-operations-at-a-glance)
- [Guides](#guides)
  - [End users](#end-users)
  - [Food scans](#food-scans)
  - [Foods](#foods)
  - [Food logs](#food-logs)
  - [Glucose](#glucose)
  - [Restaurants](#restaurants)
  - [Credits](#credits)
  - [Client tokens](#client-tokens)
- [Recipes](#recipes)
- [Calling from a mobile app](#calling-from-a-mobile-app)
- [Error handling](#error-handling)
- [Retries and timeouts](#retries-and-timeouts)
- [Async usage](#async-usage)
- [Advanced usage](#advanced-usage)
- [Type safety and forward compatibility](#type-safety-and-forward-compatibility)
- [Versioning](#versioning)
- [Development](#development)
- [Contributing](#contributing)
- [Support](#support)
- [License](#license)

## Getting an API key

1. Open the **[Developer Dashboard](https://dashboard.january.ai)** and sign up, or sign in to an
   existing account.
2. Create an API key. It looks like `sk-...`.
3. **Copy it now.** The full value is shown exactly once, at creation: January stores only a hash
   of it and can never show it to you again. Lost it? Create another and delete the old one.
4. Put it in your environment, where the SDK finds it without being told:

   ```bash
   export JANUARY_API_KEY=sk-...
   ```

An `sk-` key authenticates your whole account, so keep it on machines you control: your backend, a
server-side job, a notebook on your laptop. To call the API from a phone or a browser, mint a
[client token](#client-tokens) instead; never ship an `sk-` key inside an app.

Your plan comes with a monthly credit allowance. One successful `/v1.2` call costs 1 credit and
failed calls cost nothing. Checking the balance is itself free, always answers (including once the
allowance is spent), and tells you when it resets:

```python
from january_ai import January

with January() as client:
    credits = client.credits.get()
    print(credits.plan, credits.used_credits, "used,", credits.remaining_credits, "left")
    print("resets at", credits.resets_at)
```

## Installation

```bash
pip install january-ai
```

```bash
uv add january-ai
```

> **Not published to PyPI yet.** The first release has not been uploaded, so the two commands above
> currently fail with `No matching distribution found for january-ai`. Until then, install from a
> checkout:
>
> ```bash
> git clone https://github.com/januaryai/python-sdk
> cd python-sdk
> pip install .
> ```

**Python 3.10 or newer** (tested on 3.10, 3.11, 3.12, 3.13, and 3.14).

| Dependency | Constraint | Used for |
| --- | --- | --- |
| `httpx` | `>=0.25,<1` | HTTP transport, sync and async |
| `pydantic` | `>=2.7,<3` | Response models |
| `pillow` | `>=10` | Preparing photos for `scan_photo` |
| `anyio` | `>=4` | Sleeping between retries on asyncio *and* trio |

Pillow is imported lazily, only when a local image actually has to be decoded, so a process that
never scans a photo, or that passes a URL, a `data:` URI, or `preprocess=False`, does not pay for
it.

## Quickstart

With `JANUARY_API_KEY` exported, this runs exactly as printed. No image, no account setup, no
end-user identifier, just a sentence:

```python
from january_ai import January

client = January()  # reads JANUARY_API_KEY

scan = client.food_scans.scan_text("a bowl of oatmeal with honey and blueberries")

print(scan.meal_name)
for detection in scan.detections:
    calories = detection.food.nutrients.calories
    print(f"  {detection.food.name}: {calories.value if calories else '-'} kcal")
```

You get the meal name and one line per recognized food. Nutrient keys are optional, because the
database omits what it has no value for; that is what the `if calories` guards.
`scan.total_nutrients` carries the same panel, aggregated across the whole meal.

A photo is the same call with a different verb, and returns the same `ScanResult`:

```python
scan = client.food_scans.scan_photo("lunch.jpg")  # a photo of your own, in JPEG/PNG/WEBP/GIF
```

When nothing is recognized, `scan.detections` is empty and `scan.total_nutrients` is `None`. That
is a result, not an error, so guard both if the input is arbitrary.

## All 18 operations at a glance

Every method below exists on `January` and, identically, on `AsyncJanuary` with `await`. Return
types live in [`january_ai.types`](#type-safety-and-forward-compatibility).

| Call | What it does | Returns |
| --- | --- | --- |
| `client.food_scans.scan_photo(image)` | Recognize the foods in a meal photo | `ScanResult` |
| `client.food_scans.scan_text(text)` | Parse a written meal description | `ScanResult` |
| `client.food_scans.correct(detections, user_input)` | Revise a scan from a plain-English correction | `ScanResult` |
| `client.foods.search(query)` | Full-text search of the food database | `FoodSearchResponse` |
| `client.foods.autocomplete(query)` | Type-ahead suggestions for a partial name | `FoodSuggestionsResponse` |
| `client.foods.get(food_id)` | One food, with every serving size it supports | `Food` |
| `client.foods.lookup_barcode(upc)` | Exact lookup by 6–14 digit barcode | `FoodSearchResponse` |
| `client.foods.suggest_alternatives(food_id)` | Healthier alternatives, diet-filtered | `FoodAlternativesResponse` |
| `client.food_logs.create(foods)` | Log a meal for an end user | `FoodLog` |
| `client.food_logs.list(start, end)` | The diary between two inclusive UTC days | `FoodLogListResponse` |
| `client.food_logs.update(log_id)` | Replace any subset of a logged meal | `FoodLog` |
| `client.food_logs.delete(log_id)` | Delete a logged meal (idempotent) | `DeleteFoodLogResponse` |
| `client.glucose.predict(user_profile=…, foods=…, start_time=…)` | Predicted glucose curve and impact score | `GlucosePredictionResponse` |
| `client.restaurants.search(query, latitude=…, longitude=…)` | Restaurants near a point, by proximity | `RestaurantSearchResponse` |
| `client.restaurants.search_menu_items(query, latitude=…, longitude=…)` | Dishes across nearby restaurants | `MenuSearchResponse` |
| `client.credits.get()` † | Allowance, usage, and reset date | `CreditsResponse` |
| `client.auth.create_client_token(end_user_id)` † | Mint a scoped, short-lived device token | `ClientTokenResponse` |
| `client.auth.revoke_client_tokens(end_user_id)` † | Revoke every token one end user holds | `None` |

Every operation also accepts `end_user_id=` and `timeout=`. † These three require your `sk-` key; a
client token cannot reach them. The other fifteen work with either credential.

## Guides

### End users

Most endpoints accept an `x-end-user-id` header: your own stable identifier, at most 64 characters,
for the person a request acts on behalf of. It is opaque to January. Set it once when a client
serves a single user:

```python
from january_ai import January

client = January(default_end_user_id="acme-user-8271")
scan = client.food_scans.scan_text("a bowl of oatmeal with honey")
```

Or pass it per call when one client serves many:

```python
scan = client.food_scans.scan_text("a bowl of oatmeal", end_user_id="acme-user-2087")
```

The parameter distinguishes three cases, which is why its default is the `NOT_GIVEN` sentinel
rather than `None`:

| You pass | Header sent |
| --- | --- |
| nothing | the client's `default_end_user_id`, if it has one |
| `end_user_id="acme-user-2087"` | `acme-user-2087`, overriding the default |
| `end_user_id=None` | no header at all, even when a default exists |

**Food-log operations require an end user.** A diary belongs to somebody, so `food_logs.create`,
`list`, `update`, and `delete` refuse to send a request without one: the SDK raises `ValueError`
locally, before any HTTP call, when the identifier is neither passed nor defaulted, and also when
you pass `None` explicitly, since "send no end user" is exactly what these endpoints reject.

Food-log and glucose operations also take `end_user_timezone`, an IANA name sent as
`x-end-user-timezone`, so a "day" resolves the way the end user experiences it:

```python
logs = client.food_logs.list(
    "2026-08-01",
    "2026-08-28",
    end_user_id="acme-user-8271",
    end_user_timezone="America/Los_Angeles",
)
print(logs.total_count)
```

A client token is bound to one end user at mint time and acts only as that user, whatever headers
the device sends.

### Food scans

Three operations, one loop: scan a photo or a sentence, show the caller what came back, hand the
detections straight back with a correction in words.

```python
from january_ai import January

client = January()

scan = client.food_scans.scan_photo("lunch.jpg")
print(scan.meal_name)
for detection in scan.detections:
    print(detection.food.name, detection.confidence_score)  # "high", "medium", "low"
```

`confidence_score` comes back on photo scans only; a text scan omits it, and a text scan carries no
`meal_name` either, since the caller already has the words.

`scan_photo` accepts a filesystem path, an http(s) URL, a `data:` URI, raw `bytes`, an open binary
file, or a `PIL.Image.Image`.

#### Image preparation

By default the SDK prepares a local image before sending it. Preparation:

- applies the **EXIF orientation**, so a photo taken sideways is analyzed upright;
- caps the **longest side at 1024 px**, preserving aspect ratio and never upscaling;
- re-encodes as **JPEG at quality 85**, stepping down to 75 then 65 if it is still over budget;
- **flattens alpha onto white**, so a transparent PNG does not become a black background;
- **strips all metadata, EXIF GPS coordinates included**, because re-encoding writes none back — the
  one exception is a modest ICC profile on an image that is already RGB, which is carried across so
  a wide-gamut photo does not shift colour at exactly the size where downscaling starts.

Every bullet describes the re-encode. An image that is already compliant (JPEG, PNG, WEBP, or
non-animated GIF, within the size limits, upright, and neither CMYK nor a high-bit-depth mode)
is sent as-is rather than re-encoded, so a well-formed thumbnail is not degraded for nothing. Such
an image keeps whatever it arrived with: a small transparent PNG is forwarded as a PNG with its
alpha intact, and its metadata, EXIF GPS included, goes with it. To guarantee a strip, hand the SDK a
`PIL.Image.Image` (`scan_photo(Image.open(path))`), which always re-encodes.

**HEIC/HEIF and AVIF are not decodable out of the box.** The default iPhone camera format needs a
plugin Pillow does not ship. Install `pillow-heif`, call `pillow_heif.register_heif_opener()` once
at startup, and the SDK converts the photo to JPEG like any other non-compliant input. Without it,
`scan_photo` raises a `ValueError` that names HEIC and says the same. The API itself accepts only
JPEG, PNG, WEBP, and non-animated GIF, so converting is the only route either way.

**URLs and `data:` URIs pass through untouched**, byte for byte, whatever `preprocess` says. A URL
must be publicly fetchable server-side, since January downloads it; hosts that block hotlinking or
require a login cannot be read.

```python
scan = client.food_scans.scan_photo("https://cdn.example.com/meals/1042.jpg")
```

Turn preparation off for an image you already know is compliant:

```python
from pathlib import Path

jpeg_bytes = Path("lunch.jpg").read_bytes()
scan = client.food_scans.scan_photo(jpeg_bytes, preprocess=False)
```

With `preprocess=False` the SDK sniffs the format from the leading magic bytes, checks the size, and
counts the frames of a GIF or WEBP so an animation is refused here rather than after the upload —
but decodes nothing: no rotation, no resizing, no alpha flattening. A `PIL.Image.Image` has no
encoded form of its own and always requires `preprocess=True`.

**Size limits.** The API rejects request bodies over **5 MB** with a `413`. Base64 inflates a
payload by about 33%, so keep raw images under about **3.5 MB** before encoding, which is exactly
the budget the SDK's preparation targets. Anything that cannot be made to fit raises `ValueError`
before a request is built.

Run the same conversion yourself to cache prepared images, or to see what would be sent:

```python
from january_ai import prepare_image

image = prepare_image("lunch.jpg")  # -> "data:image/jpeg;base64,..."
print(image[:30], len(image))
```

#### Correcting a scan

Send the detections back with a sentence describing what was wrong. Adjust portions through the
sentence rather than editing serving quantities by hand:

```python
corrected = client.food_scans.correct(
    scan.detections,
    "the rice was about half that much, and there was no butter",
    meal_name=scan.meal_name,
)
print([detection.food.name for detection in corrected.detections])
```

`detections` accepts the `Detection` models a scan returned or plain mappings; models are serialized
back to the shape they arrived in, unknown fields included. Each detection needs at least one
serving. Corrections cost a credit, like any other successful call.

### Foods

```python
from january_ai import January

client = January()

results = client.foods.search("greek yogurt", category="branded", limit=10)
print(results.total_count)
for food in results.items:
    print(food.id, food.name, food.brand_name)
```

`category` is `general`, `branded`, or `recipe` and defaults to `general`; `limit` runs from 1 to 40
and defaults to 10. `total_count` is counted up to a ceiling of 250, so 250 means "250 or more"
rather than an exact total.

**There is no paging.** The API exposes no offset and no cursor, so results past `limit` cannot be
retrieved at all: raise `limit` or narrow the query rather than trying to walk the result set.

For a type-ahead field, `autocomplete` is the lighter call: it returns id, name, brand, a thumbnail,
and calories only, its `category` is narrower (`general` or `branded`), and its `limit` runs from 1
to 20 and defaults to 8.

```python
suggestions = client.foods.autocomplete("gree", limit=5)
for suggestion in suggestions.items:
    print(suggestion.id, suggestion.name, suggestion.brand_name)
```

`items` comes back empty for fewer than two characters, for no match, and for a search-index error:
the suggestion service fails open so a typing user is never interrupted.

Once a food is chosen, fetch it for the **complete list of serving sizes**. Search, barcode, and
scan results carry a single default serving; the full record is what lets an end user pick "1 cup"
against "100 g" against "1 medium".

```python
food = client.foods.get(suggestions.items[0].id)
for serving in food.servings:
    print(serving.id, serving.quantity, serving.unit, "primary" if serving.is_primary else "")
```

Barcode lookup takes a 6 to 14 digit UPC-E, UPC-A, EAN-8, EAN-13, or GTIN-14 and returns the same
envelope as a search:

```python
matches = client.foods.lookup_barcode("049000006346")
if matches.items:
    print(matches.items[0].name, matches.items[0].brand_name)
```

Healthier alternatives honour dietary restrictions and preferences. An empty result is valid: it
means no suitable alternative was found.

```python
alternatives = client.foods.suggest_alternatives(
    food.id,
    diet_restrictions=["dairy", "gluten"],
    diet_preferences=["high_protein"],
)
for alternative in alternatives.alternatives:
    calories = alternative.food.nutrients.calories
    print(alternative.food.name, calories.value if calories else "-")
```

The vocabularies are closed sets, checked by your type checker before you ship:
`types.DietRestriction` (`gluten`, `lactose`, `dairy`, `tree_nuts`, `peanuts`, `soy`, `eggs`,
`shellfish`, `fish`, `wheat`, `sesame`, `sulfites`, `yeast`, `mushrooms`, `msg`, `caffeine`,
`fodmaps`) and `types.DietPreference` (`vegetarian`, `vegan`, `keto`, `paleo`, `pescatarian`,
`low_carbohydrate`, `high_protein`, `kosher`, `halal`).

### Food logs

A food log is built from food and serving ids that came from a search, a scan, or a detection.
Every food-log call needs an [end user](#end-users).

```python
from datetime import datetime, timezone

from january_ai import January

client = January(default_end_user_id="acme-user-8271")
food = client.foods.get(511)

log = client.food_logs.create(
    [{"id": food.id, "serving": {"id": food.servings[0].id, "quantity": 1.5}}],
    name="Breakfast",
    timestamp_utc=datetime.now(timezone.utc),
)
print(log.id)  # save this to update or delete the log
```

`timestamp_utc` takes a timezone-aware `datetime` or an ISO-8601 string; a naive `datetime` raises
`ValueError` rather than guessing a zone. Omit it to mean now. At most 100 foods per log, and at
most 10000 of any one serving.

Listing takes two inclusive UTC calendar days, as `date`, `datetime`, or `YYYY-MM-DD` strings. A
`datetime` contributes only its date part, since the endpoint takes days rather than instants.

```python
from datetime import date

logs = client.food_logs.list(date(2026, 8, 1), date(2026, 8, 28))
for entry in logs.items:
    print(entry.timestamp_utc, entry.name, len(entry.foods), "foods")
```

Updates replace whichever fields you send, and only those; an argument you leave out is absent from
the PATCH body entirely rather than sent as null:

```python
updated = client.food_logs.update(log.id, name="Brunch")
print(updated.name)
```

`name=None` is the exception: it sends an explicit JSON `null` rather than omitting the field. The
API does not document how it treats a null `name`, so confirm the result before relying on it to
clear a label.

Deletion is idempotent: an unknown or already-deleted id returns the same success response, so it is
safe to retry.

```python
result = client.food_logs.delete(log.id)
print(result.status)
```

### Glucose

Predict the glucose curve a meal will produce, with no sensor involved:

```python
from datetime import datetime, timezone

from january_ai import January
from january_ai.types import FoodSelectionParam, GlucoseUserProfileParam

client = January()
food = client.foods.get(511)

profile: GlucoseUserProfileParam = {
    "age": 41,
    "sex": "male",
    "height": {"value": 70, "unit": "in"},
    "weight": {"value": 180, "unit": "lb"},
    "activity_level": "lightly_active",
    "health_conditions": ["prediabetes"],
}
meal: list[FoodSelectionParam] = [
    {"id": food.id, "serving": {"id": food.servings[0].id, "quantity": 1}}
]

prediction = client.glucose.predict(
    user_profile=profile,
    foods=meal,
    start_time=datetime.now(timezone.utc),
    end_user_id="acme-user-8271",
)

print(prediction.impact_score)  # "low", "medium", or "high"
for point in prediction.prediction:
    print(point.minutes, point.value)  # minutes after start_time, mg/dL
```

`age`, `sex`, `height`, and `weight` are required; `activity_level` and `health_conditions` are
optional. The curve comes back at 15-minute intervals. `prediction.chart` carries suggested Y-axis
bounds for plotting (`min` and `max`, not the extremes of the curve itself); the API documents
`max` as 180 when `health_conditions` includes type 2 diabetes and 140 otherwise.

To personalize against real history, send `cgm_data` and `consumed_foods` together. Each requires
the other, and the model wants at least five complete days of paired history. Send
`end_user_timezone` with them, since the history is bucketed into the end user's local days.

```python
prediction = client.glucose.predict(
    user_profile=profile,
    foods=meal,
    start_time=datetime.now(timezone.utc),
    cgm_data=[{"timestamp": "2026-08-27T08:00:00Z", "value": 94.0}],
    consumed_foods=[
        {
            "timestamp": "2026-08-27T08:05:00Z",
            "id": food.id,
            "serving": {"id": food.servings[0].id, "quantity": 1},
        }
    ],
    end_user_id="acme-user-8271",
    end_user_timezone="America/Los_Angeles",
)
print(prediction.impact_score, prediction.chart.min, prediction.chart.max)
```

Every timestamp must carry a timezone: a `datetime` must be aware, a string must carry a designator.
Type 1 diabetes is not supported by the prediction model.

### Restaurants

Search restaurants around a point, ranked by proximity. `radius` and the distances in the results
are in meters; `radius` runs from 1 to 50000 and defaults to 8000 (about five miles), and `limit`
runs from 1 to 100 and defaults to 10.

```python
from january_ai import January

client = January()

nearby = client.restaurants.search(
    "sweetgreen",
    latitude=37.7749,
    longitude=-122.4194,
    radius=5000,
    limit=10,
)
for item in nearby.items:
    print(item.name, item.distance, item.address1, item.city)
```

When the name matches no restaurant, results may come back as dishes instead, so check `item.type`,
which is either `restaurant` or `menu_item`.

To search dishes across nearby restaurants, with their nutrition:

```python
dishes = client.restaurants.search_menu_items(
    "burrito bowl",
    latitude=37.7749,
    longitude=-122.4194,
    radius=5000,
)
for dish in dishes.items:
    print(dish.name, dish.restaurant_name, dish.distance)
```

### Credits

One successful `/v1.2` call costs 1 credit. Failed calls cost nothing, and checking the balance is
itself free: it always answers, including once the allowance is exhausted:

```python
from january_ai import January

with January() as client:
    credits = client.credits.get()
    print(credits.plan, credits.period_start, "to", credits.period_end)
    print(credits.used_credits, "used of", credits.included_credits)
    print(credits.remaining_credits, "left, resets at", credits.resets_at)
```

`included_credits` and `remaining_credits` are `None` on a plan with no ceiling. When the allowance
runs out, other endpoints answer `429` with code `credit_limit_exceeded`, which the SDK raises as
`CreditLimitExceededError` and never retries. This endpoint takes your `sk-` key only.

### Client tokens

A client token (`ct-...`) is bound to exactly one end user, carries only the scopes you grant it,
and expires within two hours. Mint it on your backend and relay it to the device; see
[Calling from a mobile app](#calling-from-a-mobile-app) for the whole picture.

```python
from january_ai import January

client = January()

token = client.auth.create_client_token(
    "acme-user-8271",
    scopes=["foods:read", "food_scans:write"],
    ttl_seconds=1800,
)
print(token.token)  # the credential, returned exactly once - it is stored only as a hash
print(token.expires_in)  # seconds until expiry; prefer this over expires_at on a device
print(token.scopes, token.end_user_id)
```

Grant only the scopes the screen needs. The full set is `foods:read`, `food_scans:write`,
`food_logs:read`, `food_logs:write`, `glucose:read`, and `restaurants:read`; omit `scopes` to grant
all of them. `ttl_seconds` runs from 300 to 7200 and defaults to 1800.

| Scope | Unlocks |
| --- | --- |
| `foods:read` | `foods.search`, `autocomplete`, `get`, `lookup_barcode`, `suggest_alternatives` |
| `food_scans:write` | `food_scans.scan_photo`, `scan_text`, `correct` |
| `food_logs:read` | `food_logs.list` |
| `food_logs:write` | `food_logs.create`, `update`, `delete` |
| `glucose:read` | `glucose.predict` |
| `restaurants:read` | `restaurants.search`, `search_menu_items` |

When a device is lost or an account is deleted, revoke everything that user holds:

```python
client.auth.revoke_client_tokens("acme-user-8271")  # returns None; safe to call repeatedly
```

Revocation takes effect within 60 seconds, the authentication cache window, and one call stops at
most 500 tokens, so call it again if a user somehow holds more. Both `auth` methods require your
`sk-` key: a client token cannot mint or revoke tokens.

## Recipes

The multi-step flows a real integration needs: too long for the quickstart, too short for an
[example file](https://github.com/januaryai/python-sdk/tree/main/examples).

### Scan a photo, correct it, then log it

The conversational loop end to end. `correct` takes the scan's `detections` back exactly as they
arrived, and the log is built from the food and serving ids the scan already handed you.

```python
from datetime import datetime, timezone

from january_ai import January

client = January(default_end_user_id="acme-user-8271")

scan = client.food_scans.scan_photo("lunch.jpg")
print("scanned:", [d.food.name for d in scan.detections])

corrected = client.food_scans.correct(
    scan.detections,
    "there was about half that much rice, and no butter",
    meal_name=scan.meal_name,
)
print("corrected:", [d.food.name for d in corrected.detections])

log = client.food_logs.create(
    [
        {"id": d.food.id, "serving": {"id": d.food.servings[0].id, "quantity": 1}}
        for d in corrected.detections
        if d.food.id is not None
    ],
    name=corrected.meal_name,
    timestamp_utc=datetime.now(timezone.utc),
)
print("logged", log.id, "with", len(log.foods), "foods")
```

`Detection.food.id` is optional in the schema: a detection the database could not resolve carries
`None` and cannot be logged, which is what the filter above is for.

### Search, pick a serving, then log

The type-ahead path: a few characters, the full record for its servings, then the diary.

```python
from january_ai import January

client = January(default_end_user_id="acme-user-8271")

suggestions = client.foods.autocomplete("gree", limit=5)
food = client.foods.get(suggestions.items[0].id)

serving = next((s for s in food.servings if s.is_primary), food.servings[0])
print("logging", food.name, "as", serving.quantity, serving.unit)

log = client.food_logs.create(
    [{"id": food.id, "serving": {"id": serving.id, "quantity": 1}}],
    name="Snack",
)
calories = log.foods[0].nutrients.calories
print(log.id, "logged,", calories.value if calories else "-", "kcal")
```

### Check credits before an expensive batch

Credits are consumed per successful call, so a batch of 500 scans costs 500. Check first and stop
early rather than discovering the ceiling halfway through.

```python
from january_ai import CreditLimitExceededError, January

client = January(default_end_user_id="acme-user-8271")
meals = ["oatmeal with blueberries", "chicken caesar salad", "two slices of pizza"]

balance = client.credits.get()  # free, and does not consume a credit itself
if balance.remaining_credits is not None and balance.remaining_credits < len(meals):
    raise SystemExit(
        f"{balance.remaining_credits} credits left, {len(meals)} needed; "
        f"the allowance resets at {balance.resets_at}"
    )

for meal in meals:
    try:
        scan = client.food_scans.scan_text(meal)
    except CreditLimitExceededError as exc:
        print("allowance spent mid-batch, stopping:", exc)
        break
    print(meal, "->", len(scan.detections), "foods")
```

`remaining_credits` is `None` on a plan with no ceiling, which is why the check is explicit about
it. Keep the `CreditLimitExceededError` handler anyway: another process on the same account can
spend the balance between the check and the batch.

## Calling from a mobile app

Two credentials reach this API, and the difference matters.

| | `sk-` API key | `ct-` client token |
| --- | --- | --- |
| Authenticates | your whole account | exactly one end user |
| Lives | on your servers | on a device |
| Lifetime | until you delete it | up to 2 hours (`ttl_seconds` 300–7200) |
| Scopes | everything | only what you granted |
| Can reach | all 18 operations | the 15 that are not `auth` or `credits` |
| Revocable per user | no | yes, `auth.revoke_client_tokens` |

> **Never ship an `sk-` key in an app.** Putting one inside a mobile app, a desktop app, or browser
> JavaScript puts a credential for every one of your end users into every copy you distribute,
> where it can be extracted from the binary or read out of network traffic. There is no obfuscation
> that fixes this. Mint a client token instead.

Your backend already knows who the user is. Add one endpoint that turns that into a January
credential, behind the same login as the rest of your API:

```python
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from january_ai import January, JanuaryError

app = FastAPI()
january = January()  # reads JANUARY_API_KEY; one client for the whole process


class DeviceToken(BaseModel):
    token: str
    expires_in: int
    scopes: list[str]


def current_user_id() -> str:
    """Your existing session or JWT check. Must never trust a user-supplied id."""
    return "acme-user-8271"


@app.post("/january-token", response_model=DeviceToken)
def january_token(user_id: str = Depends(current_user_id)) -> DeviceToken:
    """Mint a short-lived January credential for the logged-in user."""
    try:
        token = january.auth.create_client_token(
            user_id,
            scopes=["foods:read", "food_scans:write", "food_logs:read", "food_logs:write"],
            ttl_seconds=900,
        )
    except JanuaryError as exc:
        raise HTTPException(status_code=502, detail="could not mint a January token") from exc
    return DeviceToken(token=token.token, expires_in=token.expires_in, scopes=token.scopes)
```

The device then calls January **directly**, with no proxy of your own in the request path, so photos
never transit your servers and your backend does not carry the latency of a 120-second scan. On the
device, the token is just an API key, and a `401` is the signal to fetch a fresh one and retry once:

```python
from collections.abc import Callable

from january_ai import AuthenticationError, January
from january_ai.types import ScanResult


def scan_on_device(fetch_token: Callable[[], str], image: str) -> ScanResult:
    """`fetch_token()` calls your /january-token endpoint and returns the `ct-...` value."""
    client = January(fetch_token())
    try:
        return client.food_scans.scan_photo(image)
    except AuthenticationError:
        client = January(fetch_token())  # expired or revoked; mint a fresh one and retry once
        return client.food_scans.scan_photo(image)
```

Keep the TTL short and mint per session, not per request. Grant only the scopes that screen needs:
a photo-logging screen wants `food_scans:write` and `food_logs:write`, and has no reason to hold
`glucose:read`.

## Error handling

Everything that goes wrong once a request is under way derives from `JanuaryError`: transport
failures, error responses, and a response body that did not match its schema. So does a missing API
key. **Mistakes in the arguments you pass do not.** They raise the standard Python exceptions, so a
photo path that came from runtime data needs a handler of its own:

| Local mistake, before anything is sent | Raised |
| --- | --- |
| A naive `datetime`; a missing `end_user_id` on a food log; an `end_user_id` or `end_user_timezone` that is not printable ASCII (HTTP header values cannot carry more); an image that cannot be prepared, is animated, is closed, is already at EOF, or is too large; a negative `max_retries`; a `base_url` with no scheme | `ValueError` |
| A `date` or an epoch number where a timestamp belongs; an unsupported `image` type; a file handle opened in text mode | `TypeError` |
| An image path that does not exist | `FileNotFoundError` |

Everything else:

| Exception | HTTP status | API `code` | Retried automatically? |
| --- | --- | --- | --- |
| `JanuaryError` | — | — | No — the base class. Also raised directly for a missing API key, a closed client, a 3xx redirect, and a success body that is empty or not JSON |
| `APIConnectionError` | — | — | Yes, when the request never left the client |
| `APITimeoutError` | — | — | Yes, unless the operation forbids an ambiguous replay |
| `APIResponseValidationError` | 2xx | — | No — valid JSON, but not the expected shape |
| `BadRequestError` | 400 | `invalid_request` | No |
| `AuthenticationError` | 401 | `unauthorized`, `token_expired` | No |
| `PermissionDeniedError` | 403 | `forbidden` | No |
| `NotFoundError` | 404 | `not_found` | No |
| `PayloadTooLargeError` | 413 | `payload_too_large` | No |
| `RateLimitError` | 429 | `rate_limited` | Yes, honouring `Retry-After` |
| `CreditLimitExceededError` | 429 | `credit_limit_exceeded` | **Never** |
| `InternalServerError` | 5xx | `internal_error`, `upstream_error`, `service_unavailable`, `upstream_timeout` | Yes, except where an ambiguous replay is forbidden |
| `InternalServerError` | 501 | `not_implemented` | No — permanent until the feature ships |
| `APIStatusError` | any other (409, 422, …) | unknown | Only when the status is 429, 500, 502, 503, or 504 |

Every `APIStatusError` carries `status_code`, `code`, `message`, `docs_url`, `request_id` (the
`x-request-id` response header, worth quoting in a support ticket), `response`, the parsed `body`,
and `retry_after` — the `Retry-After` header in seconds, or `None` when the server sent none. That
last one is read on every status, so a 503 telling you to back off for ten minutes says so without
your having to reach into the raw headers. A `message` longer than 200 characters is truncated with
a marker; the untruncated value stays in `body`.

`CreditLimitExceededError` is deliberately **not** a subclass of `RateLimitError`, even though both
arrive as a 429: a sleep-and-retry handler written for rate limits must not silently swallow an
exhausted monthly allowance. Only the error `code` tells the two apart, which is why distinguishing
them is the point of this example:

```python
import time

from january_ai import (
    CreditLimitExceededError,
    January,
    JanuaryError,
    RateLimitError,
)

client = January()
path = "lunch.jpg"  # runtime data, so it can be wrong

try:
    scan = client.food_scans.scan_photo(path)
except (FileNotFoundError, ValueError, TypeError) as exc:
    print("bad input; nothing was sent:", exc)
except CreditLimitExceededError as exc:
    # Retrying cannot help: the allowance returns at the start of the next calendar month.
    print("monthly allowance spent:", exc, "- resets at", client.credits.get().resets_at)
except RateLimitError as exc:
    # The SDK already retried within its budget; this is what is left over.
    print("still rate limited after retries; server suggested", exc.retry_after, "seconds")
    time.sleep(exc.retry_after or 60)
except JanuaryError as exc:
    print("request failed:", exc)
else:
    print(scan.meal_name)
```

Retry decisions are made on `code` first, exactly as the API documents. A code the SDK knows to be
permanent is never retried even when its status happens to be retryable; only an unknown or missing
code falls through to the status class, so a code added to the API after this release still behaves
sensibly.

## Retries and timeouts

**Retries.** Failed requests are retried up to `max_retries` times beyond the first attempt, which
defaults to **2**. The delay is exponential with jitter: 0.5s doubling per attempt, capped at 8s,
multiplied by a random factor between 0.75 and 1.0. Every attempt builds a fresh request.

```python
from january_ai import January

client = January(max_retries=5)  # or 0 to disable retrying entirely
print(client.credits.get().plan)
```

**`Retry-After` is honoured, within a budget.** A retryable error response normally carries the
header, typically a 429 or a 503, either as an integer number of seconds or as an HTTP-date; the SDK
then waits exactly that long instead of using its own backoff. Two bounds apply, and crossing either
raises the error immediately so your process decides what to do rather than parking a worker inside
one call:

| Bound | Value | Applies to |
| --- | --- | --- |
| Per wait | 60s | One `Retry-After`. A longer one is never slept through at all. |
| Per call | 60s | The sum of every `Retry-After` wait across the attempts of a single call. |

So a server answering `Retry-After: 60` to everything is honoured once and then raises, and one
answering `Retry-After: 20` is honoured three times — either way a single method call spends at most
60 seconds waiting on the server's instruction, whatever `max_retries` is set to. The raised error
says which bound was hit and how long the server had asked for, and `.retry_after` carries the
server's value on **every** status class, not just `RateLimitError`, so you can schedule the retry
yourself.

The SDK's own exponential backoff is not charged to that budget. It is bounded already — 8s per wait
and roughly 15s in total at `max_retries=5` — and truncating it would cut short exactly the retries
that make a transient 5xx survivable.

**`CreditLimitExceededError` is never retried.** Credit exhaustion carries no `Retry-After` and the
allowance returns at the start of the next calendar month, so retrying cannot succeed.

**Transport failures are classified before they are replayed.** A connection error, a connect
timeout, a pool timeout, or a proxy rejecting the tunnel all mean the request never reached the
server, so it is always safe to send again. A read timeout or a dropped connection mid-flight is
ambiguous, since the server may have processed the request already, and is replayed only where a
duplicate is harmless. Anything else, such as a malformed URL, is never retried.

> ### Caveat: `food_logs.create` does not retry ambiguous failures
>
> The API has no idempotency key for it, so a replayed create risks writing the meal twice. Two
> things count as ambiguous, and both are raised rather than retried:
>
> - a **post-send transport failure** (`APITimeoutError`, `APIConnectionError`);
> - a **retryable 5xx**: a `502`, `503`, or `504 upstream_timeout` is a gateway telling you it
>   forwarded the request to an origin whose fate it does not know, which is the same hazard seen
>   from the other end of the wire.
>
> If you see either, call `food_logs.list` for that day and check before retrying it yourself. A
> `429` is still retried, because rate limiting is refused before the handler runs and so cannot
> have written anything; pre-send failures, where nothing was ever transmitted, are still retried
> too; and every other operation retries ambiguous failures as usual.

**Timeouts.** The default is 60 seconds overall with a 5-second connect timeout. The three
food-scan operations get **120 seconds** instead, because they run model inference server-side. The
timeout in force is decided in this order:

| Priority | Timeout | Applies to |
| --- | --- | --- |
| 1 | `timeout=` passed on the call | that call only, always wins |
| 2 | 120s overall / 5s connect | `scan_photo`, `scan_text`, `correct` |
| 3 | `timeout=` passed to the client | every other operation |
| 4 | 60s overall / 5s connect | every other operation, when the client set none |

> **A client-level `timeout=` does not reach the three scan operations.** `January(timeout=5.0)`
> still allows `scan_photo` its 120 seconds, and `January(timeout=300.0)` does not extend it either:
> the operation's own default outranks the client's, in both directions. Pass `timeout=` on the call,
> the only lever that changes a scan's budget.

```python
import httpx

from january_ai import January

client = January(timeout=30.0)  # every phase of every call, except the three scans
client = January(timeout=httpx.Timeout(30.0, connect=5.0))  # the same, with per-phase control

scan = client.food_scans.scan_photo("lunch.jpg", timeout=45.0)  # the only way to bound a scan
print(scan.meal_name)
```

A timeout is per attempt, not per call: with retries enabled, a call can take up to
`(max_retries + 1)` times the timeout plus the backoff between attempts.

## Async usage

`AsyncJanuary` mirrors `January` exactly: same configuration, same resources, same method names,
same arguments, same return types. Porting between them is a matter of adding `await`:

```python
import asyncio

from january_ai import AsyncJanuary


async def main() -> None:
    async with AsyncJanuary() as client:
        results = await client.foods.search("greek yogurt", limit=5)
        for food in results.items:
            print(food.id, food.name)


asyncio.run(main())
```

Scans take seconds, so overlap them. `asyncio.gather` over one client is the whole technique:

```python
import asyncio

from january_ai import AsyncJanuary

MEALS = [
    "a bowl of oatmeal with honey and blueberries",
    "chicken caesar salad with croutons",
    "two slices of pepperoni pizza and a diet coke",
]


async def main() -> None:
    async with AsyncJanuary(default_end_user_id="acme-user-8271") as client:
        scans = await asyncio.gather(*(client.food_scans.scan_text(meal) for meal in MEALS))
        for meal, scan in zip(MEALS, scans):
            print(f"{meal}: {len(scan.detections)} foods")


asyncio.run(main())
```

The async client works on asyncio and on trio, since it sleeps through `anyio`. Build one client and
reuse it: it owns a connection pool, and a client per call pays for a new TLS handshake every time.
Unlike the synchronous client, an async client is bound to the event loop it was created on, so
create it inside your async entry point rather than at import time, and do not share one instance
across loops.

For photo scans, the async client runs image preparation (decoding, rotating, resizing, and
re-encoding, all of it CPU-bound) on a worker thread, so a batch of scans does not block the event
loop. The synchronous client is safe to share across threads.

## Advanced usage

**Bring your own httpx client** to share a connection pool, route through a proxy, or install a
custom transport. A client you pass in belongs to you and is never closed by `close()` or
`aclose()`:

```python
import httpx

from january_ai import January

http_client = httpx.Client(proxy="http://127.0.0.1:8080", limits=httpx.Limits(max_connections=20))
client = January(http_client=http_client)
```

**Point at a different origin** for staging or a proxy of your own, by argument or by the
`JANUARY_BASE_URL` environment variable. Trailing slashes are stripped, a path prefix is preserved,
and `/v1.2` is still appended. The scheme is checked at construction, so a forgotten `https://`
raises `ValueError` where the mistake is rather than at the first request. The SDK does not follow
redirects: an origin that answers `301` is reported as such, naming both the `Location` and your
`base_url`.

```python
staging = January(base_url="https://staging.partners.january.ai")
print(staging)  # January(base_url='https://staging.partners.january.ai', api_key='sk-***')
```

`repr()` redacts the credential to its kind, `'sk-***'` or `'ct-***'`, so a client that lands in a
log line or a traceback does not leak it.

**Add your own headers.** They are merged into every request. The SDK's headers take precedence,
with one exception: a `User-Agent` you supply is kept, so you can identify your own traffic without
breaking authentication.

```python
client = January(default_headers={"User-Agent": "acme-app/2.1", "X-Acme-Tenant": "eu-1"})
```

**Logging.** The SDK logs to the `january_ai` logger and emits retry decisions at `DEBUG`:

```python
import logging

logging.basicConfig()
logging.getLogger("january_ai").setLevel(logging.DEBUG)
```

Nothing that could carry a secret is passed to it: no `Authorization` header, no API key, no request
body (a photo-scan body is a base64 image), and no query string, which on some endpoints carries the
end user's identifier. Raise the *root* level rather than this logger's and you also switch on
`httpx` and `httpcore`, which log full request URLs, query string included, putting back exactly
what the line above strips out.

**Close the client** when you are done, or use it as a context manager:

```python
with January() as client:
    print(client.credits.get().remaining_credits)
```

Both `close()` and `aclose()` are idempotent, and a client that has been closed raises `JanuaryError`
rather than a bare `RuntimeError` from httpx if you keep using it.

## Type safety and forward compatibility

The package ships a `py.typed` marker, so mypy and pyright type-check against it with no stubs. The
SDK is checked under `mypy --strict` and every public signature is annotated, including the
`Literal` vocabularies, so a misspelled `category` or `diet_preference` is caught in your editor.

**Model names match the API reference.** Each is the OpenAPI schema name with the `Dto` suffix
removed, so `FoodDto` is `types.Food` and `GlucosePredictionResponseDto` is
`types.GlucosePredictionResponse`. Types ending in `Param` are the `TypedDict` shapes you pass *in*;
everything else is a parsed response. A dict literal written at the call site is inferred against
the right `Param` automatically; one built into a variable first needs the annotation
(`profile: types.GlucoseUserProfileParam = {...}`), or it infers as `dict[str, object]` and your
type checker rejects it.

```python
from january_ai import types


def summarize(scan: types.ScanResult) -> str:
    return ", ".join(detection.food.name for detection in scan.detections)
```

**Unknown fields never break your build.** Every response model allows extra fields, so a key the
server starts returning after this release parses without error and is reachable through the model
rather than dropped. For the same reason, enum-like fields on *responses* are typed `str` rather
than a closed literal: a new `confidence_score` or `impact_score` value cannot turn a successful
response into a validation error. Request parameters do use literals, where a typo is worth catching
before it ships.

## Versioning

**The SDK follows semantic versioning.** Breaking changes to the public API (the names exported
from `january_ai` and `january_ai.types`, method signatures, and documented behaviour) only land in
a major release. Names prefixed with an underscore are internal and may change at any time.

**This release targets API version `/v1.2`.** Every request path carries that prefix, and the models
in `january_ai.types` describe the `/v1.2` schemas. A future API version will arrive as a new SDK
major release rather than by changing what this one sends.

Release notes live in [CHANGELOG.md](https://github.com/januaryai/python-sdk/blob/main/CHANGELOG.md).

## Development

```bash
git clone https://github.com/januaryai/python-sdk
cd python-sdk
uv sync

uv run pytest        # tests, no network
uv run ruff check .  # lint
uv run ruff format . # format
uv run mypy          # type-check, strict, over src/ tests/ examples/
```

Runnable examples live in [`examples/`](https://github.com/januaryai/python-sdk/tree/main/examples):

| Example | Shows |
| --- | --- |
| [`01_scan_photo.py`](https://github.com/januaryai/python-sdk/blob/main/examples/01_scan_photo.py) | Recognizing a meal from a photo, and correcting it |
| [`02_search_and_log.py`](https://github.com/januaryai/python-sdk/blob/main/examples/02_search_and_log.py) | Searching foods, picking a serving, logging a meal |
| [`03_glucose_prediction.py`](https://github.com/januaryai/python-sdk/blob/main/examples/03_glucose_prediction.py) | Predicting a glucose curve for a meal |
| [`04_client_tokens.py`](https://github.com/januaryai/python-sdk/blob/main/examples/04_client_tokens.py) | Minting and revoking client tokens for a device |
| [`05_async_concurrent_scans.py`](https://github.com/januaryai/python-sdk/blob/main/examples/05_async_concurrent_scans.py) | Scanning several photos concurrently with `asyncio.gather` |

## Contributing

Contributions are welcome. [CONTRIBUTING.md](https://github.com/januaryai/python-sdk/blob/main/CONTRIBUTING.md)
covers the sync/async parity rule and how models map to the OpenAPI spec. To report a vulnerability,
see [SECURITY.md](https://github.com/januaryai/python-sdk/blob/main/SECURITY.md).

## Support

- Questions about the API or your account: [support@january.ai](mailto:support@january.ai)
- Community: [Discord](https://discord.gg/cYQeh3UnC)
- Bugs in this SDK: [GitHub issues](https://github.com/januaryai/python-sdk/issues)

## License

MIT. See [LICENSE](https://github.com/januaryai/python-sdk/blob/main/LICENSE).
