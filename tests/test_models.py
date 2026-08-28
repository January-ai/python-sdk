"""Tests for the models in :mod:`january_ai.types` and the input serializers behind them.

The models are a transcription of ``.spec/openapi.json``, so most of what is worth checking can be
checked against the spec itself rather than against a second hand-written copy of it. The tests
below load the schema file and drive from it: every schema must have a model under the documented
name, every model must accept a realistic payload covering every property the schema declares, and
every field must be required exactly when the schema says it is. What the spec cannot express - that
an unknown field survives a round trip, that a timestamp comes back timezone-aware, that a detection
handed straight back to the corrections endpoint arrives unchanged - is checked directly.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Final, get_args

import pytest
from pydantic import ValidationError

from january_ai import types
from january_ai._serialize import serialize_detections, to_iso_date, to_iso_datetime
from january_ai.types import CorrectScan, Detection, Food, ScanResult
from january_ai.types._base import JanuaryModel

SPEC_PATH: Final = Path(__file__).resolve().parent.parent / ".spec" / "openapi.json"


# --------------------------------------------------------------------------------------------
# The spec
# --------------------------------------------------------------------------------------------


def _load_schemas() -> dict[str, dict[str, object]]:
    """Read every component schema out of the checked-in OpenAPI document."""
    with SPEC_PATH.open(encoding="utf-8") as handle:
        document: object = json.load(handle)
    assert isinstance(document, dict)
    components = document["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    return schemas


SCHEMAS: Final = _load_schemas()
SCHEMA_NAMES: Final = sorted(SCHEMAS)


def properties_of(schema_name: str) -> set[str]:
    """Return the property names a schema declares."""
    declared = SCHEMAS[schema_name].get("properties", {})
    assert isinstance(declared, dict)
    return set(declared)


def required_of(schema_name: str) -> set[str]:
    """Return the property names a schema marks required."""
    declared = SCHEMAS[schema_name].get("required", [])
    assert isinstance(declared, list)
    return set(declared)


def model_for(schema_name: str) -> type[JanuaryModel]:
    """Look up the SDK model for a schema by the documented naming rule: drop the ``Dto`` suffix."""
    model = getattr(types, schema_name.removesuffix("Dto"), None)
    assert isinstance(model, type), f"no model exported for {schema_name}"
    assert issubclass(model, JanuaryModel)
    return model


# --------------------------------------------------------------------------------------------
# Realistic payloads, one per schema, covering every declared property
# --------------------------------------------------------------------------------------------


def amount(value: float, unit: str) -> dict[str, object]:
    """Build a nutrient reading."""
    return {"value": value, "unit": unit}


NUTRIENTS: Final[dict[str, object]] = {
    "calories": amount(146, "kcal"),
    "protein": amount(20.0, "g"),
    "carbohydrates": amount(7.9, "g"),
    "net_carbohydrates": amount(7.9, "g"),
    "total_fat": amount(3.8, "g"),
    "trans_fat": amount(0.0, "g"),
    "saturated_fat": amount(2.4, "g"),
    "fiber": amount(0.0, "g"),
    "total_sugars": amount(7.0, "g"),
    "added_sugars": amount(0.0, "g"),
    "cholesterol": amount(15.0, "mg"),
    "calcium": amount(230.0, "mg"),
    "iron": amount(0.1, "mg"),
    "potassium": amount(282.0, "mg"),
    "sodium": amount(68.0, "mg"),
    "vitamin_d": amount(0.0, "IU"),
}

MACRO_NUTRIENTS: Final[dict[str, object]] = {
    "calories": amount(158, "kcal"),
    "protein": amount(5.9, "g"),
    "carbohydrates": amount(27.3, "g"),
    "net_carbohydrates": amount(23.3, "g"),
    "total_fat": amount(3.2, "g"),
    "saturated_fat": amount(0.6, "g"),
    "fiber": amount(4.0, "g"),
    "total_sugars": amount(1.1, "g"),
    "added_sugars": amount(0.0, "g"),
    "sodium": amount(9.0, "mg"),
}

FOOD_SERVING: Final[dict[str, object]] = {
    "id": 68051535,
    "quantity": 1,
    "unit": "oz",
    "scaling_factor": 1,
    "weight_grams": 28,
    "is_primary": True,
}

FOOD: Final[dict[str, object]] = {
    "id": 101963552,
    "name": "Dipped Banana Bites",
    "brand_name": "Banana",
    "nutrients": NUTRIENTS,
    "glycemic_index": 11.3,
    "glycemic_load": 1.4,
    "image_url": "https://cdn.january.ai/foods/101963552.jpg",
    "upc": "049000006346",
    "servings": [FOOD_SERVING],
}

SERVING_SELECTION: Final[dict[str, object]] = {"id": 68051535, "quantity": 1.4}

FOOD_SELECTION: Final[dict[str, object]] = {"id": 101963552, "serving": SERVING_SELECTION}

SERVING_SUMMARY: Final[dict[str, object]] = {"id": 34237662, "quantity": 1, "unit": "cup"}

ALTERNATIVE_FOOD: Final[dict[str, object]] = {
    "id": 70379835,
    "name": "Oatmeal",
    "brand_name": "",
    "nutrients": MACRO_NUTRIENTS,
    "servings": [SERVING_SUMMARY],
}

DETECTION_SERVING: Final[dict[str, object]] = {
    "id": 34237662,
    "quantity": 1,
    "unit": "cup",
    "selected_quantity": 1,
}

DETECTION_FOOD: Final[dict[str, object]] = {
    "id": 70379835,
    "name": "Oatmeal",
    "brand_name": "",
    "nutrients": MACRO_NUTRIENTS,
    "servings": [DETECTION_SERVING],
}

DETECTION: Final[dict[str, object]] = {"confidence_score": "high", "food": DETECTION_FOOD}

SERVING_DETAILS: Final[dict[str, object]] = {
    "id": 68051535,
    "quantity": 1,
    "unit": "cup",
    "weight_grams": 245,
}

LOGGED_FOOD: Final[dict[str, object]] = {
    "id": 101963552,
    "name": "Greek Yogurt, Plain, Whole Milk",
    "brand_name": None,
    "image_url": None,
    "glycemic_index": 11.3,
    "glycemic_load": 1.4,
    "nutrients": NUTRIENTS,
    "consumed_serving": SERVING_SELECTION,
    "serving_details": SERVING_DETAILS,
}

FOOD_LOG: Final[dict[str, object]] = {
    "id": "78129823-8ba2-4183-b13b-71f0e963c606",
    "foods": [LOGGED_FOOD],
    "timestamp_utc": "2024-09-13T11:34:56Z",
    "name": "Breakfast",
}

HEIGHT: Final[dict[str, object]] = {"value": 66, "unit": "in"}

WEIGHT: Final[dict[str, object]] = {"value": 150, "unit": "lb"}

GLUCOSE_USER_PROFILE: Final[dict[str, object]] = {
    "age": 42,
    "sex": "female",
    "height": HEIGHT,
    "weight": WEIGHT,
    "activity_level": "moderately_active",
    "health_conditions": ["prediabetes"],
}

CGM_READING: Final[dict[str, object]] = {"timestamp": "2024-09-10T08:15:00Z", "value": 104}

CONSUMED_FOOD_ENTRY: Final[dict[str, object]] = {
    "timestamp": "2024-09-10T08:00:00Z",
    "id": 101963552,
    "serving": SERVING_SELECTION,
}

RESTAURANT_RESULT: Final[dict[str, object]] = {
    "type": "restaurant",
    "id": "53fc3b8a-e6bf-404d-83c8-9f42124d1bee",
    "name": "McDonald's",
    "is_chain": False,
    "distance": 124,
    "city": "San Francisco",
    "address1": "123 Main Street",
    "address2": "Suite 100",
}

MENU_ITEM: Final[dict[str, object]] = {
    "type": "menu_item",
    "id": "228990954",
    "name": "burger",
    "restaurant_name": "morning due cafe",
    "is_chain": False,
    "nutrients": NUTRIENTS,
    "glycemic_index": 51.0,
    "glycemic_load": 18.4,
    "image_url": "https://cdn.january.ai/menu-items/228990954.jpg",
    "distance": 124,
    "servings": [FOOD_SERVING],
}

PAYLOADS: Final[dict[str, dict[str, object]]] = {
    "ApiErrorDto": {
        "message": "query is required: the food name to search for, e.g. ?query=greek yogurt.",
        "code": "invalid_request",
        "docs_url": "https://docs.january.ai/rest-api/api-overview",
    },
    "CreateClientTokenDto": {
        "end_user_id": "acme-user-8271",
        "scopes": ["foods:read", "food_logs:write"],
        "ttl_seconds": 1800,
    },
    "ClientTokenResponseDto": {
        "token": "ct-4fQr7yNb2KcXm9TvLpZ3wHs6JdRg8AeYuQ1oViB5xCn",
        "expires_in": 1800,
        "expires_at": "2026-08-26T18:35:11.000Z",
        "end_user_id": "acme-user-8271",
        "scopes": [
            "foods:read",
            "food_scans:write",
            "food_logs:read",
            "food_logs:write",
            "glucose:read",
            "restaurants:read",
        ],
    },
    "CreditsResponseDto": {
        "plan": "free",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "resets_at": "2026-09-01T00:00:00.000Z",
        "included_credits": 1000,
        "used_credits": 342,
        "remaining_credits": 658,
    },
    "NutrientAmountDto": amount(300, "g"),
    "NutrientsDto": NUTRIENTS,
    "MacroNutrientsDto": MACRO_NUTRIENTS,
    "FoodServingDto": FOOD_SERVING,
    "FoodDto": FOOD,
    "FoodSearchResponseDto": {"total_count": 132, "items": [FOOD]},
    "FoodSuggestionDto": {
        "id": 70376053,
        "name": "greek yogurt",
        "brand_name": "Chobani",
        "image_url": "https://cdn.january.ai/foods/70376053.jpg",
        "nutrients": NUTRIENTS,
    },
    "FoodSuggestionsResponseDto": {
        "items": [{"id": 70376053, "name": "greek yogurt"}],
    },
    "FoodAlternativesRequestDto": {
        "diet_restrictions": ["lactose", "gluten"],
        "diet_preferences": ["high_protein"],
    },
    "ServingSummaryDto": SERVING_SUMMARY,
    "AlternativeFoodDto": ALTERNATIVE_FOOD,
    "FoodAlternativeDto": {"food": ALTERNATIVE_FOOD},
    "FoodAlternativesResponseDto": {"alternatives": [{"food": ALTERNATIVE_FOOD}]},
    "RestaurantResultDto": RESTAURANT_RESULT,
    "RestaurantSearchResponseDto": {"total_count": 12, "items": [RESTAURANT_RESULT]},
    "MenuItemDto": MENU_ITEM,
    "MenuSearchResponseDto": {"total_count": 7, "items": [MENU_ITEM]},
    "ScanPhotoDto": {"image": "https://cdn.example.com/meals/lunch.jpg"},
    "ScanTextDto": {"text": "a bowl of oatmeal with honey and a banana"},
    "CorrectScanDto": {
        "meal_name": "Breakfast Bowl",
        "detections": [DETECTION],
        "user_input": "it was about half of that",
    },
    "DetectionServingDto": DETECTION_SERVING,
    "DetectionFoodDto": DETECTION_FOOD,
    "DetectionDto": DETECTION,
    "ScanResultDto": {
        "meal_name": "Breakfast Bowl",
        "total_nutrients": MACRO_NUTRIENTS,
        "detections": [DETECTION],
    },
    "ServingSelectionDto": SERVING_SELECTION,
    "FoodSelectionDto": FOOD_SELECTION,
    "CreateFoodLogDto": {
        "foods": [FOOD_SELECTION],
        "timestamp_utc": "2024-09-13T07:34:56-04:00",
        "name": "Breakfast",
    },
    "ServingDetailsDto": SERVING_DETAILS,
    "LoggedFoodDto": LOGGED_FOOD,
    "FoodLogDto": FOOD_LOG,
    "FoodLogListResponseDto": {"total_count": 3, "items": [FOOD_LOG]},
    "UpdateFoodLogDto": {
        "foods": [FOOD_SELECTION],
        "timestamp_utc": "2024-09-13T11:34:56Z",
        "name": "Second breakfast",
    },
    "DeleteFoodLogResponseDto": {"status": "deleted"},
    "HeightDto": HEIGHT,
    "WeightDto": WEIGHT,
    "GlucoseUserProfileDto": GLUCOSE_USER_PROFILE,
    "CgmReadingDto": CGM_READING,
    "ConsumedFoodEntryDto": CONSUMED_FOOD_ENTRY,
    "GlucosePredictDto": {
        "user_profile": GLUCOSE_USER_PROFILE,
        "foods": [FOOD_SELECTION],
        "start_time": "2024-09-10T12:30:00Z",
        "cgm_data": [CGM_READING],
        "consumed_foods": [CONSUMED_FOOD_ENTRY],
    },
    "GlucosePredictionPointDto": {"minutes": 30, "value": 140},
    "GlucoseChartDto": {"min": 70, "max": 140},
    "GlucosePredictionResponseDto": {
        "prediction": [{"minutes": 0, "value": 96}, {"minutes": 30, "value": 140}],
        "impact_score": "low",
        "chart": {"min": 70, "max": 140},
    },
}


def payload(schema_name: str) -> dict[str, object]:
    """Return a fresh, independently mutable payload for a schema."""
    return deepcopy(PAYLOADS[schema_name])


def dict_at(body: dict[str, object], key: str) -> dict[str, object]:
    """Read a nested object out of a payload."""
    value = body[key]
    assert isinstance(value, dict)
    return value


def list_at(body: dict[str, object], key: str) -> list[dict[str, object]]:
    """Read a nested array of objects out of a payload."""
    value = body[key]
    assert isinstance(value, list)
    return value


# --------------------------------------------------------------------------------------------
# The models against the spec
# --------------------------------------------------------------------------------------------


def test_every_schema_has_a_model_named_by_the_documented_rule() -> None:
    """A name read in the API reference is importable from ``january_ai.types`` verbatim."""
    for schema_name in SCHEMA_NAMES:
        model = model_for(schema_name)
        assert model.__name__ == schema_name.removesuffix("Dto")
        assert model.__name__ in types.__all__


def test_every_schema_has_a_payload() -> None:
    """The suite covers all 46 schemas, so a schema added later fails here rather than silently."""
    assert set(PAYLOADS) == set(SCHEMA_NAMES)
    assert len(SCHEMA_NAMES) == 46


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_model_validates_a_realistic_payload(schema_name: str) -> None:
    """Every model parses a body covering every property its schema declares."""
    body = payload(schema_name)
    assert set(body) == properties_of(schema_name), "payload does not cover the schema"

    instance = model_for(schema_name).model_validate(body)

    assert instance.model_extra == {}
    for field in properties_of(schema_name):
        assert hasattr(instance, field)


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_required_and_optional_fields_match_the_spec(schema_name: str) -> None:
    """Dropping a required property fails; dropping an optional one does not."""
    model = model_for(schema_name)
    body = payload(schema_name)
    required = required_of(schema_name)

    for field in sorted(properties_of(schema_name)):
        without = {key: value for key, value in body.items() if key != field}
        if field in required:
            with pytest.raises(ValidationError):
                model.model_validate(without)
        else:
            model.model_validate(without)


def test_a_required_nullable_field_accepts_null_but_not_absence() -> None:
    """``weight_grams`` is required and nullable: ``None`` is a value, missing is an error."""
    body = payload("FoodServingDto")
    body["weight_grams"] = None

    assert types.FoodServing.model_validate(body).weight_grams is None

    with pytest.raises(ValidationError, match="weight_grams"):
        types.FoodServing.model_validate({k: v for k, v in body.items() if k != "weight_grams"})


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_unknown_fields_survive_validation_and_round_trip(schema_name: str) -> None:
    """A field the server adds after this release is kept, reachable, and dumped back out."""
    body = payload(schema_name)
    body["x_added_after_this_release"] = {"note": "forward compatibility", "weight": 3}

    instance = model_for(schema_name).model_validate(body)

    extra = instance.model_extra
    assert extra is not None
    assert extra["x_added_after_this_release"] == {"note": "forward compatibility", "weight": 3}
    for mode in ("python", "json"):
        dumped = instance.model_dump(mode=mode)
        assert dumped["x_added_after_this_release"] == {
            "note": "forward compatibility",
            "weight": 3,
        }


def test_unknown_fields_survive_inside_nested_models() -> None:
    """Forward compatibility reaches all the way down, not only the top-level object."""
    body = payload("FoodDto")
    dict_at(body, "nutrients")["choline"] = {"value": 38.0, "unit": "mg"}
    list_at(body, "servings")[0]["household_measure"] = "1 oz (about 4 pieces)"

    food = Food.model_validate(body)
    dumped = food.model_dump()

    assert dumped["nutrients"]["choline"] == {"value": 38.0, "unit": "mg"}
    assert dumped["servings"][0]["household_measure"] == "1 oz (about 4 pieces)"


@pytest.mark.parametrize(
    ("schema_name", "field", "value"),
    [
        ("GlucosePredictionResponseDto", "impact_score", "catastrophic"),
        ("DetectionDto", "confidence_score", "very_high"),
        ("RestaurantResultDto", "type", "food_truck"),
        ("DeleteFoodLogResponseDto", "status", "already_deleted"),
    ],
)
def test_unknown_enum_values_still_parse(schema_name: str, field: str, value: str) -> None:
    """Enum-ish response fields are plain strings, so a new server value cannot break parsing."""
    body = payload(schema_name)
    body[field] = value

    assert getattr(model_for(schema_name).model_validate(body), field) == value


# --------------------------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------------------------

DATETIME_FIELDS: Final = [
    ("CgmReadingDto", "timestamp"),
    ("ClientTokenResponseDto", "expires_at"),
    ("ConsumedFoodEntryDto", "timestamp"),
    ("CreateFoodLogDto", "timestamp_utc"),
    ("FoodLogDto", "timestamp_utc"),
    ("GlucosePredictDto", "start_time"),
]


def mentions_datetime(annotation: object) -> bool:
    """Report whether a field annotation resolves to ``datetime`` anywhere inside it."""
    if annotation is datetime:
        return True
    return any(mentions_datetime(argument) for argument in get_args(annotation))


def test_datetime_field_inventory_is_complete() -> None:
    """The list below names every ``datetime`` field, so a new one cannot go unchecked."""
    found = {
        (schema_name, field)
        for schema_name in SCHEMA_NAMES
        for field, info in model_for(schema_name).model_fields.items()
        if mentions_datetime(info.annotation)
    }

    assert found == set(DATETIME_FIELDS)


@pytest.mark.parametrize(("schema_name", "field"), DATETIME_FIELDS)
def test_datetime_fields_parse_to_aware_datetimes(schema_name: str, field: str) -> None:
    """Every timestamp the API returns carries an offset, and keeps it through validation."""
    instance = model_for(schema_name).model_validate(payload(schema_name))

    parsed = getattr(instance, field)
    assert isinstance(parsed, datetime)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None


def test_a_non_utc_offset_is_preserved_rather_than_normalized() -> None:
    """An offset the caller sent is kept as sent; only the instant it names has to be right."""
    body = payload("CreateFoodLogDto")
    body["timestamp_utc"] = "2024-09-13T07:34:56-04:00"

    parsed = types.CreateFoodLog.model_validate(body).timestamp_utc

    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=-4)
    assert parsed.astimezone(timezone.utc).hour == 11


def test_datetime_fields_dump_back_to_iso_strings() -> None:
    """A parsed log dumps to JSON the API would accept back."""
    log = types.FoodLog.model_validate(payload("FoodLogDto"))

    dumped = log.model_dump(mode="json")

    assert isinstance(dumped["timestamp_utc"], str)
    # Pydantic renders a UTC instant with a trailing "Z", which datetime.fromisoformat only
    # learned to read in 3.11; the package supports 3.10, so parse it the way 3.10 can.
    normalized = dumped["timestamp_utc"].replace("Z", "+00:00")
    assert datetime.fromisoformat(normalized).utcoffset() == timedelta(0)


# --------------------------------------------------------------------------------------------
# Detections round-tripping into a correction
# --------------------------------------------------------------------------------------------


def test_detections_round_trip_into_a_corrections_body() -> None:
    """A scan result handed straight back to ``corrections`` arrives with everything it had."""
    scan_body = payload("ScanResultDto")
    detection = list_at(scan_body, "detections")[0]
    detection["detector_version"] = "vision-2026-05"
    food = dict_at(detection, "food")
    food["source_database"] = "usda-2025"
    list_at(food, "servings")[0]["household_measure"] = "1 cup cooked"

    scan = ScanResult.model_validate(scan_body)
    serialized = serialize_detections(scan.detections)

    serialized_food = dict_at(serialized[0], "food")
    assert serialized[0]["detector_version"] == "vision-2026-05"
    assert serialized_food["source_database"] == "usda-2025"
    assert list_at(serialized_food, "servings")[0]["household_measure"] == "1 cup cooked"
    assert serialized[0]["confidence_score"] == "high"
    # The body has to survive json.dumps: it is going straight into a request.
    assert json.loads(json.dumps(serialized)) == serialized

    corrected = CorrectScan.model_validate(
        {
            "meal_name": scan.meal_name,
            "detections": serialized,
            "user_input": "the oatmeal was half a cup",
        }
    )

    round_tripped = corrected.detections[0].model_dump(mode="json", exclude_none=True)
    assert round_tripped == serialized[0]


def test_serialize_detections_drops_nulls_and_keeps_mappings_as_they_are() -> None:
    """Models are dumped without their ``None`` holes; raw mappings are copied untouched."""
    body = payload("DetectionDto")
    body["confidence_score"] = None
    model = Detection.model_validate(body)

    from_model, from_mapping = serialize_detections([model, {"food": {"name": "toast"}}])

    assert "confidence_score" not in from_model
    assert from_mapping == {"food": {"name": "toast"}}


def test_serialize_detections_copies_rather_than_aliases_a_mapping() -> None:
    """The returned body is the caller's to keep; mutating it must not touch their input."""
    original: dict[str, object] = {"food": {"name": "toast"}}

    serialized = serialize_detections([original])
    serialized[0]["user_edited"] = True

    assert original == {"food": {"name": "toast"}}


# --------------------------------------------------------------------------------------------
# Input serialization
# --------------------------------------------------------------------------------------------


def test_to_iso_datetime_rejects_a_naive_datetime() -> None:
    """A timestamp with no offset would be read in a timezone the caller never chose."""
    with pytest.raises(ValueError, match="timezone-aware") as caught:
        to_iso_datetime(datetime(2024, 9, 13, 11, 34, 56), field="timestamp_utc")

    assert "timestamp_utc" in str(caught.value)


def test_to_iso_datetime_accepts_an_aware_datetime() -> None:
    """A UTC timestamp renders with its offset spelled out."""
    value = datetime(2024, 9, 13, 11, 34, 56, tzinfo=timezone.utc)

    assert to_iso_datetime(value, field="timestamp_utc") == "2024-09-13T11:34:56+00:00"


def test_to_iso_datetime_accepts_any_offset() -> None:
    """UTC is not required, only an explicit offset."""
    value = datetime(2024, 9, 13, 7, 34, 56, tzinfo=timezone(timedelta(hours=-4)))

    assert to_iso_datetime(value, field="start_time") == "2024-09-13T07:34:56-04:00"


@pytest.mark.parametrize(
    "value",
    ["2024-09-13T11:34:56Z", "2024-09-13T07:34:56-04:00", "not a timestamp at all"],
)
def test_to_iso_datetime_passes_strings_through(value: str) -> None:
    """A caller who formatted the timestamp themselves is not second-guessed."""
    assert to_iso_datetime(value, field="timestamp_utc") == value


@pytest.mark.parametrize(
    ("value", "type_name", "hint"),
    [
        (date(2024, 9, 1), "date", "names a day rather than an instant"),
        (1725192000, "int", "datetime.fromtimestamp"),
        (1725192000.5, "float", "datetime.fromtimestamp"),
        (None, "NoneType", ""),
    ],
    ids=["date", "epoch-int", "epoch-float", "none"],
)
def test_to_iso_datetime_rejects_a_type_it_cannot_render(
    value: object, type_name: str, hint: str
) -> None:
    """Name the argument and the accepted types rather than reaching for ``.tzinfo`` and failing.

    A ``date`` is the likely mistake, since the SDK takes one wherever a calendar day is meant.
    Left unchecked it produced ``AttributeError: 'datetime.date' object has no attribute 'tzinfo'``,
    which names neither this SDK nor the parameter to fix.
    """
    with pytest.raises(TypeError) as caught:
        to_iso_datetime(value, field="timestamp_utc")  # type: ignore[arg-type]

    message = str(caught.value)
    assert "timestamp_utc" in message
    assert "timezone-aware datetime or an ISO 8601 string" in message
    assert f"got {type_name}" in message
    assert hint in message


def test_to_iso_datetime_still_accepts_a_datetime_subclass() -> None:
    """The type check must not refuse the very thing it is guarding, however it was subclassed."""

    class Stamp(datetime):
        pass

    value = Stamp(2024, 9, 13, 11, 34, 56, tzinfo=timezone.utc)

    assert to_iso_datetime(value, field="timestamp_utc") == "2024-09-13T11:34:56+00:00"


def test_to_iso_date_renders_a_date() -> None:
    """A calendar date renders as ``YYYY-MM-DD``."""
    assert to_iso_date(date(2024, 9, 13), field="start") == "2024-09-13"


def test_to_iso_date_takes_only_the_date_part_of_a_datetime() -> None:
    """``datetime`` subclasses ``date``, so the time part has to be dropped deliberately."""
    late = datetime(2024, 9, 13, 23, 59, 59, tzinfo=timezone.utc)

    assert to_iso_date(late, field="end") == "2024-09-13"


def test_to_iso_date_accepts_a_naive_datetime() -> None:
    """A day is not an instant, so no offset is demanded here."""
    assert to_iso_date(datetime(2024, 9, 13, 1, 2, 3), field="start") == "2024-09-13"


def test_to_iso_date_passes_strings_through() -> None:
    """An already-formatted date is forwarded unchanged."""
    assert to_iso_date("2024-09-13", field="start") == "2024-09-13"
