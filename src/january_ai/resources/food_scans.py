"""Recognize a meal from a photo or a sentence, then correct the result in plain English.

The three operations here form one loop. Scan a photo or a written description, show the caller
what came back, then hand the detections straight back with a correction in words - "it was about
half of that" - rather than editing serving quantities by hand. All three run model inference
server-side and are far slower than the rest of the API, so they carry
:data:`~january_ai._constants.SCAN_TIMEOUT` in place of the client's default timeout.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import partial
from typing import cast

import httpx
from anyio import to_thread

from .._base_client import AsyncAPIClient, SyncAPIClient
from .._constants import API_VERSION_PATH, SCAN_TIMEOUT
from .._images import prepare_image
from .._serialize import serialize_detections
from .._types import NOT_GIVEN, ImageInput, NotGiven, RequestSpec
from ..types import Detection, ScanResult

__all__ = ["AsyncFoodScans", "FoodScans"]

_PHOTO_PATH = f"{API_VERSION_PATH}/food-scans/photo"
_TEXT_PATH = f"{API_VERSION_PATH}/food-scans/text"
_CORRECTIONS_PATH = f"{API_VERSION_PATH}/food-scans/corrections"


def _scan_photo_spec(
    image: str,
    *,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[ScanResult]:
    """Describe a photo scan whose image has already been converted to an API-ready string."""
    return RequestSpec(
        method="POST",
        path=_PHOTO_PATH,
        cast_to=ScanResult,
        json_body={"image": image},
        end_user_id=end_user_id,
        timeout=timeout,
        default_timeout=SCAN_TIMEOUT,
    )


def _scan_text_spec(
    text: str,
    *,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[ScanResult]:
    """Describe a text scan."""
    return RequestSpec(
        method="POST",
        path=_TEXT_PATH,
        cast_to=ScanResult,
        json_body={"text": text},
        end_user_id=end_user_id,
        timeout=timeout,
        default_timeout=SCAN_TIMEOUT,
    )


def _correct_spec(
    detections: Sequence[Detection | Mapping[str, object]],
    user_input: str,
    *,
    meal_name: str | None,
    end_user_id: str | NotGiven | None,
    timeout: float | httpx.Timeout | NotGiven,
) -> RequestSpec[ScanResult]:
    """Describe a correction, serializing the detections back into the shape the scan returned."""
    body: dict[str, object] = {
        "detections": serialize_detections(detections),
        "user_input": user_input,
    }
    if meal_name is not None:
        body["meal_name"] = meal_name
    return RequestSpec(
        method="POST",
        path=_CORRECTIONS_PATH,
        cast_to=ScanResult,
        json_body=body,
        end_user_id=end_user_id,
        timeout=timeout,
        default_timeout=SCAN_TIMEOUT,
    )


class FoodScans:
    """Photo scans, text scans, and plain-English corrections to a scan result.

    Reached as ``client.food_scans``. With a client token, these operations need the
    ``food_scans:write`` scope.
    """

    def __init__(self, client: SyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    def scan_photo(
        self,
        image: ImageInput,
        *,
        preprocess: bool = True,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> ScanResult:
        """Analyze a meal photo and return the foods detected in it.

        Each detection carries its own nutrition, and the result also carries an aggregated total
        for the meal. Reading packaged-food labels (Nutrition Facts panels) is not supported yet;
        until it is, look packaged foods up by barcode with ``client.foods.lookup_barcode``.
        Analysis can take tens of seconds for a complex meal, which is why this call allows longer
        than the client's default timeout.

        ``image`` is converted by :func:`january_ai.prepare_image` before the request is built, so
        an unreadable, animated, or oversized image raises ``ValueError`` without anything being
        sent.

        Args:
            image: An http(s) URL, a ``data:`` URI, a filesystem path, raw bytes, a binary file
                object, or a ``PIL.Image.Image``. A URL is forwarded untouched and must be
                publicly fetchable server-side; hosts that block hotlinking or require a login
                cannot be read.
            preprocess: Whether to decode, rotate, downscale, and re-encode the image before
                sending. Turning it off skips the decode, and with it the EXIF rotation and the
                size check, so use it only for an image already known to be a compliant JPEG, PNG,
                WEBP, or non-animated GIF within the size limit.
            end_user_id: The end user this scan acts on behalf of. Omitted uses the client's
                ``default_end_user_id``; an explicit ``None`` sends no end user at all.
            timeout: Override the timeout for this call.

        Returns:
            The detected foods and the meal totals. An empty ``detections`` list means nothing was
            recognized, which is a result rather than an error.

        Raises:
            ValueError: If the image cannot be read, is animated, or is too large to encode.
            APIStatusError: If the API rejects the request, including a 413 when the encoded body
                exceeds 5 MB.
        """
        return cast(
            ScanResult,
            self._client.send(
                _scan_photo_spec(
                    prepare_image(image, preprocess=preprocess),
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )

    def scan_text(
        self,
        text: str,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> ScanResult:
        """Parse a written meal description into detected foods with quantities and nutrition.

        The text counterpart of :meth:`scan_photo`. A text scan carries no ``meal_name``, since the
        caller already has the words. For keyword search over the food database rather than parsing
        a sentence, use ``client.foods.search``.

        Args:
            text: Natural language describing what was eaten, such as "a bowl of oatmeal with
                honey and a banana". At most 512 characters.
            end_user_id: The end user this scan acts on behalf of. Omitted uses the client's
                ``default_end_user_id``; an explicit ``None`` sends no end user at all.
            timeout: Override the timeout for this call.

        Returns:
            The detected foods and the meal totals.

        Raises:
            APIStatusError: If the API rejects the request, including a 400 when the text is
                missing or too long.
        """
        return cast(
            ScanResult,
            self._client.send(_scan_text_spec(text, end_user_id=end_user_id, timeout=timeout)),
        )

    def correct(
        self,
        detections: Sequence[Detection | Mapping[str, object]],
        user_input: str,
        *,
        meal_name: str | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> ScanResult:
        """Revise a scan result from a plain-English description of what was wrong.

        Send back the ``detections`` and ``meal_name`` a photo or text scan returned, plus a
        sentence describing the correction; the response is a corrected result with recalculated
        totals. Adjust portions through ``user_input`` ("it was about half of that") rather than
        editing serving quantities by hand. Nutrient keys a detection omits are filled in as zero
        automatically.

        Args:
            detections: The ``detections`` array from a scan, either as the :class:`Detection`
                models it returned or as plain mappings. Models are serialized back to the shape
                they arrived in, unknown fields included. Each detection needs at least one
                serving.
            user_input: Plain English describing what to correct.
            meal_name: The meal name from the scan, when it returned one; photo scans do and text
                scans do not, and any label works for a text scan. Omitted, the API defaults it to
                "Meal".
            end_user_id: The end user this correction acts on behalf of. Omitted uses the client's
                ``default_end_user_id``; an explicit ``None`` sends no end user at all.
            timeout: Override the timeout for this call.

        Returns:
            The corrected detections and recalculated meal totals.

        Raises:
            APIStatusError: If the API rejects the request. A 400 names the exact detection index
                and the problem with it.
        """
        return cast(
            ScanResult,
            self._client.send(
                _correct_spec(
                    detections,
                    user_input,
                    meal_name=meal_name,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )


class AsyncFoodScans:
    """Photo scans, text scans, and plain-English corrections to a scan result.

    Reached as ``client.food_scans``. With a client token, these operations need the
    ``food_scans:write`` scope.
    """

    def __init__(self, client: AsyncAPIClient) -> None:
        """Bind the resource to the client that sends its requests."""
        self._client = client

    async def scan_photo(
        self,
        image: ImageInput,
        *,
        preprocess: bool = True,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> ScanResult:
        """Analyze a meal photo and return the foods detected in it.

        Each detection carries its own nutrition, and the result also carries an aggregated total
        for the meal. Reading packaged-food labels (Nutrition Facts panels) is not supported yet;
        until it is, look packaged foods up by barcode with ``client.foods.lookup_barcode``.
        Analysis can take tens of seconds for a complex meal, which is why this call allows longer
        than the client's default timeout.

        Decoding and re-encoding an image is CPU-bound work that Pillow performs synchronously, so
        it runs in a worker thread and does not stall the event loop. It still happens before the
        request is built, so an unreadable, animated, or oversized image raises ``ValueError``
        without anything being sent.

        Args:
            image: An http(s) URL, a ``data:`` URI, a filesystem path, raw bytes, a binary file
                object, or a ``PIL.Image.Image``. A URL is forwarded untouched and must be
                publicly fetchable server-side; hosts that block hotlinking or require a login
                cannot be read.
            preprocess: Whether to decode, rotate, downscale, and re-encode the image before
                sending. Turning it off skips the decode, and with it the EXIF rotation and the
                size check, so use it only for an image already known to be a compliant JPEG, PNG,
                WEBP, or non-animated GIF within the size limit.
            end_user_id: The end user this scan acts on behalf of. Omitted uses the client's
                ``default_end_user_id``; an explicit ``None`` sends no end user at all.
            timeout: Override the timeout for this call.

        Returns:
            The detected foods and the meal totals. An empty ``detections`` list means nothing was
            recognized, which is a result rather than an error.

        Raises:
            ValueError: If the image cannot be read, is animated, or is too large to encode.
            APIStatusError: If the API rejects the request, including a 413 when the encoded body
                exceeds 5 MB.
        """
        # Pillow decodes and re-encodes synchronously, so this runs off the event loop thread.
        prepared = await to_thread.run_sync(partial(prepare_image, image, preprocess=preprocess))
        return cast(
            ScanResult,
            await self._client.send(
                _scan_photo_spec(prepared, end_user_id=end_user_id, timeout=timeout)
            ),
        )

    async def scan_text(
        self,
        text: str,
        *,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> ScanResult:
        """Parse a written meal description into detected foods with quantities and nutrition.

        The text counterpart of :meth:`scan_photo`. A text scan carries no ``meal_name``, since the
        caller already has the words. For keyword search over the food database rather than parsing
        a sentence, use ``client.foods.search``.

        Args:
            text: Natural language describing what was eaten, such as "a bowl of oatmeal with
                honey and a banana". At most 512 characters.
            end_user_id: The end user this scan acts on behalf of. Omitted uses the client's
                ``default_end_user_id``; an explicit ``None`` sends no end user at all.
            timeout: Override the timeout for this call.

        Returns:
            The detected foods and the meal totals.

        Raises:
            APIStatusError: If the API rejects the request, including a 400 when the text is
                missing or too long.
        """
        return cast(
            ScanResult,
            await self._client.send(
                _scan_text_spec(text, end_user_id=end_user_id, timeout=timeout)
            ),
        )

    async def correct(
        self,
        detections: Sequence[Detection | Mapping[str, object]],
        user_input: str,
        *,
        meal_name: str | None = None,
        end_user_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN,
    ) -> ScanResult:
        """Revise a scan result from a plain-English description of what was wrong.

        Send back the ``detections`` and ``meal_name`` a photo or text scan returned, plus a
        sentence describing the correction; the response is a corrected result with recalculated
        totals. Adjust portions through ``user_input`` ("it was about half of that") rather than
        editing serving quantities by hand. Nutrient keys a detection omits are filled in as zero
        automatically.

        Args:
            detections: The ``detections`` array from a scan, either as the :class:`Detection`
                models it returned or as plain mappings. Models are serialized back to the shape
                they arrived in, unknown fields included. Each detection needs at least one
                serving.
            user_input: Plain English describing what to correct.
            meal_name: The meal name from the scan, when it returned one; photo scans do and text
                scans do not, and any label works for a text scan. Omitted, the API defaults it to
                "Meal".
            end_user_id: The end user this correction acts on behalf of. Omitted uses the client's
                ``default_end_user_id``; an explicit ``None`` sends no end user at all.
            timeout: Override the timeout for this call.

        Returns:
            The corrected detections and recalculated meal totals.

        Raises:
            APIStatusError: If the API rejects the request. A 400 names the exact detection index
                and the problem with it.
        """
        return cast(
            ScanResult,
            await self._client.send(
                _correct_spec(
                    detections,
                    user_input,
                    meal_name=meal_name,
                    end_user_id=end_user_id,
                    timeout=timeout,
                )
            ),
        )
