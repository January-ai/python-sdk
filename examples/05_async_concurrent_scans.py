"""Scan several photos at once with the async client.

Demonstrates `AsyncJanuary` as an async context manager and `asyncio.gather` over concurrent
`food_scans.scan_photo` calls. Scans run model inference server-side and take tens of seconds each,
so overlapping them is the difference between a minute and a few seconds. The async client runs
image preparation - decoding, rotating, resizing, re-encoding, all CPU-bound - on a worker thread,
so a batch does not block the event loop.

Every method on `AsyncJanuary` matches its `January` counterpart exactly; the only difference is
`await`.

Usage:
    export JANUARY_API_KEY=sk-...
    python examples/05_async_concurrent_scans.py breakfast.jpg lunch.jpg dinner.jpg

Cost: 1 credit per image. Failed calls cost nothing.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from january_ai import AsyncJanuary, JanuaryError
from january_ai.types import ScanResult

DEFAULT_IMAGES = [
    "https://images.january.ai/examples/breakfast.jpg",
    "https://images.january.ai/examples/lunch.jpg",
    "https://images.january.ai/examples/dinner.jpg",
]


async def scan(client: AsyncJanuary, image: str) -> ScanResult:
    """Scan one image, letting failures propagate to `gather`."""
    return await client.food_scans.scan_photo(image, end_user_id="demo-user-1042")


def report(image: str, result: ScanResult | BaseException) -> None:
    """Print one scan outcome, whether it succeeded or raised."""
    if isinstance(result, BaseException):
        print(f"\n{image}: failed - {result}")
        return

    print(f"\n{image}: {result.meal_name or '(unnamed meal)'}")
    for detection in result.detections:
        print(f"  - {detection.food.name}")

    totals = result.total_nutrients
    if totals is not None and totals.calories is not None:
        print(f"  totals: {totals.calories.value:g} {totals.calories.unit}")


async def main() -> int:
    """Scan every image named on the command line concurrently."""
    if os.environ.get("JANUARY_API_KEY") is None:
        print("set JANUARY_API_KEY to an sk- key from https://dashboard.january.ai")
        return 2

    images = sys.argv[1:] or DEFAULT_IMAGES
    print(f"scanning {len(images)} image(s) concurrently")

    started = time.perf_counter()

    # Build the client inside the coroutine: an async client is bound to the event loop it was
    # created on. One client, reused for every request, so they share a connection pool.
    async with AsyncJanuary() as client:
        # return_exceptions keeps one bad image from cancelling the rest of the batch.
        results = await asyncio.gather(
            *(scan(client, image) for image in images),
            return_exceptions=True,
        )

    for image, result in zip(images, results, strict=False):
        report(image, result)

    elapsed = time.perf_counter() - started
    failures = sum(isinstance(result, BaseException) for result in results)
    print(f"\n{len(images) - failures}/{len(images)} scanned in {elapsed:.1f}s")

    if failures and all(isinstance(result, JanuaryError) for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
