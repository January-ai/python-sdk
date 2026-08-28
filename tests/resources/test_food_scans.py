"""Photo scans, text scans, and plain-English corrections.

The photo endpoint is the one place the SDK transforms an argument substantially, so most of this
module is about what ends up in the ``image`` field: a URL untouched, a file on disk as a data URI,
and an unreadable image rejected before a request is ever built.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from january_ai import AsyncJanuary, January
from january_ai.types import Detection, ScanResult

BASE_URL = "https://partners.january.ai"
PHOTO_URL = f"{BASE_URL}/v1.2/food-scans/photo"
TEXT_URL = f"{BASE_URL}/v1.2/food-scans/text"
CORRECTIONS_URL = f"{BASE_URL}/v1.2/food-scans/corrections"

REMOTE_PHOTO = "https://cdn.example.com/meals/lunch.jpg?sig=abc123"

DETECTION_PAYLOAD = {
    "confidence_score": "high",
    "food": {
        "id": 70379835,
        "name": "Oatmeal",
        "brand_name": "",
        "nutrients": {"calories": {"value": 300, "unit": "kcal"}},
        "servings": [{"id": 34237662, "quantity": 1, "unit": "cup"}],
    },
}
SCAN_PAYLOAD = {
    "meal_name": "Breakfast Bowl",
    "total_nutrients": {"calories": {"value": 300, "unit": "kcal"}},
    "detections": [DETECTION_PAYLOAD],
}

# What a Detection model must serialize back to: JSON mode, with unset optionals dropped so the
# API fills in the zero-value nutrient keys itself.
SERIALIZED_DETECTION = {
    "confidence_score": "high",
    "food": {
        "id": 70379835,
        "name": "Oatmeal",
        "brand_name": "",
        "nutrients": {"calories": {"value": 300.0, "unit": "kcal"}},
        "servings": [{"id": 34237662, "quantity": 1.0, "unit": "cup"}],
    },
}


def _fingerprint(request: httpx.Request) -> tuple[str, str, bytes]:
    """Reduce a request to the parts the synchronous and asynchronous clients must agree on."""
    return (request.method, str(request.url), request.content)


@pytest.mark.anyio
async def test_scan_photo_forwards_an_http_url_verbatim(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Send a hosted image's URL exactly as given, query string and all, without fetching it."""
    route = respx_mock.post(PHOTO_URL).mock(return_value=httpx.Response(200, json=SCAN_PAYLOAD))

    scan = client.food_scans.scan_photo(REMOTE_PHOTO)

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path == "/v1.2/food-scans/photo"
    assert json.loads(request.content) == {"image": REMOTE_PHOTO}

    assert isinstance(scan, ScanResult)
    assert scan.meal_name == "Breakfast Bowl"
    assert scan.detections[0].confidence_score == "high"
    assert scan.detections[0].food.name == "Oatmeal"
    assert scan.detections[0].food.servings[0].unit == "cup"
    assert scan.total_nutrients is not None
    assert scan.total_nutrients.calories is not None
    assert scan.total_nutrients.calories.value == 300.0

    await async_client.food_scans.scan_photo(REMOTE_PHOTO)
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


@pytest.mark.anyio
async def test_scan_photo_encodes_a_local_image_as_a_data_uri(
    client: January,
    async_client: AsyncJanuary,
    small_jpeg_path: Path,
    respx_mock: respx.MockRouter,
) -> None:
    """Read an image off disk and send it base64-encoded, identically from both clients."""
    route = respx_mock.post(PHOTO_URL).mock(return_value=httpx.Response(200, json=SCAN_PAYLOAD))

    client.food_scans.scan_photo(small_jpeg_path)

    body = json.loads(route.calls.last.request.content)
    assert set(body) == {"image"}
    assert body["image"].startswith("data:image/jpeg;base64,")
    assert len(body["image"]) > len("data:image/jpeg;base64,")

    await async_client.food_scans.scan_photo(small_jpeg_path)
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_scan_photo_forwards_the_preprocess_flag(
    client: January, small_jpeg_bytes: bytes, respx_mock: respx.MockRouter
) -> None:
    """Skip the decode when asked to, sending the caller's bytes with only a sniffed MIME type."""
    route = respx_mock.post(PHOTO_URL).mock(return_value=httpx.Response(200, json=SCAN_PAYLOAD))

    client.food_scans.scan_photo(small_jpeg_bytes, preprocess=False)

    assert json.loads(route.calls.last.request.content)["image"].startswith(
        "data:image/jpeg;base64,"
    )

    # With the decode skipped the failure comes from sniffing the magic bytes, which is a
    # different message from the decode failure - proof the flag reached prepare_image.
    with pytest.raises(ValueError, match="could not recognize the image format"):
        client.food_scans.scan_photo(b"not an image at all", preprocess=False)

    assert respx_mock.calls.call_count == 1


def test_scan_photo_uses_the_longer_scan_timeout(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Allow the scan endpoints longer than the client default, since they run inference."""
    route = respx_mock.post(PHOTO_URL).mock(return_value=httpx.Response(200, json=SCAN_PAYLOAD))

    client.food_scans.scan_photo(REMOTE_PHOTO)

    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["read"] == 120.0
    assert timeout["connect"] == 5.0


def test_scan_photo_rejects_an_unreadable_image_before_sending(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Raise on bad image data without making any HTTP call at all."""
    respx_mock.post(PHOTO_URL).mock(return_value=httpx.Response(200, json=SCAN_PAYLOAD))

    with pytest.raises(ValueError, match="not a readable image"):
        client.food_scans.scan_photo(b"this is a text file, not a photograph")

    assert respx_mock.calls.call_count == 0


@pytest.mark.anyio
async def test_async_scan_photo_rejects_an_unreadable_image_before_sending(
    async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Raise from the worker thread the async client decodes in, still before any request."""
    respx_mock.post(PHOTO_URL).mock(return_value=httpx.Response(200, json=SCAN_PAYLOAD))

    with pytest.raises(ValueError, match="not a readable image"):
        await async_client.food_scans.scan_photo(b"this is a text file, not a photograph")

    assert respx_mock.calls.call_count == 0


@pytest.mark.anyio
async def test_scan_text_posts_the_description(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Send the sentence as the whole body and parse the detections back."""
    route = respx_mock.post(TEXT_URL).mock(return_value=httpx.Response(200, json=SCAN_PAYLOAD))

    scan = client.food_scans.scan_text("a bowl of oatmeal with honey and a banana")

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path == "/v1.2/food-scans/text"
    assert json.loads(request.content) == {"text": "a bowl of oatmeal with honey and a banana"}

    assert isinstance(scan, ScanResult)
    assert scan.detections[0].food.id == 70379835

    await async_client.food_scans.scan_text("a bowl of oatmeal with honey and a banana")
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


@pytest.mark.anyio
async def test_correct_round_trips_detection_models(
    client: January, async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Serialize the models a scan returned back into the shape they arrived in."""
    route = respx_mock.post(CORRECTIONS_URL).mock(
        return_value=httpx.Response(200, json=SCAN_PAYLOAD)
    )
    scanned = ScanResult.model_validate(SCAN_PAYLOAD)
    assert isinstance(scanned.detections[0], Detection)

    corrected = client.food_scans.correct(
        scanned.detections, "it was about half of that", meal_name="Breakfast Bowl"
    )

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path == "/v1.2/food-scans/corrections"
    assert json.loads(request.content) == {
        "detections": [SERIALIZED_DETECTION],
        "user_input": "it was about half of that",
        "meal_name": "Breakfast Bowl",
    }
    assert isinstance(corrected, ScanResult)
    assert corrected.meal_name == "Breakfast Bowl"

    await async_client.food_scans.correct(
        scanned.detections, "it was about half of that", meal_name="Breakfast Bowl"
    )
    assert _fingerprint(route.calls[0].request) == _fingerprint(route.calls[1].request)


def test_correct_accepts_plain_mappings(client: January, respx_mock: respx.MockRouter) -> None:
    """Pass raw decoded JSON through untouched, for a caller that never built the models."""
    route = respx_mock.post(CORRECTIONS_URL).mock(
        return_value=httpx.Response(200, json=SCAN_PAYLOAD)
    )

    client.food_scans.correct([DETECTION_PAYLOAD], "half a cup, not a full one")

    assert json.loads(route.calls.last.request.content) == {
        "detections": [DETECTION_PAYLOAD],
        "user_input": "half a cup, not a full one",
    }


def test_correct_mixes_models_and_mappings(client: January, respx_mock: respx.MockRouter) -> None:
    """Accept both forms in one sequence, since a caller may have edited only one detection."""
    route = respx_mock.post(CORRECTIONS_URL).mock(
        return_value=httpx.Response(200, json=SCAN_PAYLOAD)
    )
    detection = Detection.model_validate(DETECTION_PAYLOAD)

    client.food_scans.correct([detection, DETECTION_PAYLOAD], "swap the second one for toast")

    assert json.loads(route.calls.last.request.content)["detections"] == [
        SERIALIZED_DETECTION,
        DETECTION_PAYLOAD,
    ]


def test_correct_omits_the_meal_name_when_not_given(
    client: January, respx_mock: respx.MockRouter
) -> None:
    """Leave ``meal_name`` out for a text scan, which never returned one."""
    route = respx_mock.post(CORRECTIONS_URL).mock(
        return_value=httpx.Response(200, json=SCAN_PAYLOAD)
    )

    client.food_scans.correct([DETECTION_PAYLOAD], "half a cup")

    assert "meal_name" not in json.loads(route.calls.last.request.content)
