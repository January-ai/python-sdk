"""Recognize a meal from a photo, then correct the result in plain English.

Demonstrates the blocking client, `food_scans.scan_photo`, and `food_scans.correct`. The image is
prepared by the SDK before it is sent: EXIF orientation applied, longest side capped at 1024 px,
alpha flattened onto white, re-encoded as JPEG, and all metadata (including GPS) stripped. An
http(s) URL or a `data:` URI is forwarded untouched instead.

Usage:
    export JANUARY_API_KEY=sk-...
    python examples/01_scan_photo.py path/to/lunch.jpg
    python examples/01_scan_photo.py https://cdn.example.com/meals/1042.jpg

Cost: 2 credits - one for the scan, one for the correction. Failed calls cost nothing.
"""

from __future__ import annotations

import os
import sys

from january_ai import January, JanuaryError
from january_ai.types import MacroNutrients, ScanResult


def show(scan: ScanResult, heading: str) -> None:
    """Print a scan result: its meal name, each detection, and the meal totals."""
    print(f"\n{heading}")
    print(f"  meal name: {scan.meal_name or '(none)'}")

    if not scan.detections:
        print("  nothing was recognized in this image")
        return

    for detection in scan.detections:
        food = detection.food
        label = f"{food.brand_name} {food.name}" if food.brand_name else food.name
        confidence = detection.confidence_score or "n/a"
        serving = food.servings[0]
        print(
            f"  - {label} (id={food.id}, confidence={confidence}) "
            f"[serving {serving.id}: {serving.quantity} {serving.unit}]"
        )

    print(f"  totals: {describe(scan.total_nutrients)}")


def describe(nutrients: MacroNutrients | None) -> str:
    """Render the macros that are present as a compact one-liner."""
    if nutrients is None:
        return "(none reported)"

    named = {
        "calories": nutrients.calories,
        "protein": nutrients.protein,
        "carbs": nutrients.carbohydrates,
        "fat": nutrients.total_fat,
    }
    parts = [f"{name} {amount.value:g}{amount.unit}" for name, amount in named.items() if amount]
    return ", ".join(parts) or "(none reported)"


def main() -> int:
    """Scan the image named on the command line and correct the result."""
    if os.environ.get("JANUARY_API_KEY") is None:
        print("set JANUARY_API_KEY to an sk- key from https://dashboard.january.ai")
        return 2

    image = sys.argv[1] if len(sys.argv) > 1 else "lunch.jpg"

    # The client reads JANUARY_API_KEY itself. Scans run model inference server-side and can take
    # tens of seconds, so they use a 120-second timeout rather than the client's usual 60.
    with January() as client:
        try:
            scan = client.food_scans.scan_photo(image)
        except FileNotFoundError:
            print(f"no such image: {image}")
            return 1
        except ValueError as exc:
            # Raised locally, before anything is sent: unreadable, animated, or too large to encode.
            print(f"the image could not be prepared: {exc}")
            return 1
        except JanuaryError as exc:
            print(f"the scan failed: {exc}")
            return 1

        show(scan, f"Scan of {image}")

        if not scan.detections:
            return 0

        # Corrections take the detections back exactly as they arrived. Adjust portions through the
        # sentence rather than by editing serving quantities by hand.
        corrected = client.food_scans.correct(
            scan.detections,
            "there was about half as much of the first item",
            meal_name=scan.meal_name,
        )
        show(corrected, "After the correction")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
