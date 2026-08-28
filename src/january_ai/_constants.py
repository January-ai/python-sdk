"""Tunable constants shared by the transport, retry, and image-preparation layers.

Covers the API endpoint defaults, request timeouts, the retry/backoff envelope, the limits the
food-scan endpoints impose on uploaded images, and the environment variables the client reads.
"""

from __future__ import annotations

from typing import Final

import httpx

DEFAULT_BASE_URL: Final = "https://partners.january.ai"
API_VERSION_PATH: Final = "/v1.2"

DEFAULT_TIMEOUT: Final = httpx.Timeout(60.0, connect=5.0)
# Photo and text scans run model inference server-side and are far slower than the other endpoints.
SCAN_TIMEOUT: Final = httpx.Timeout(120.0, connect=5.0)

DEFAULT_MAX_RETRIES: Final = 2
INITIAL_RETRY_DELAY: Final = 0.5
MAX_RETRY_DELAY: Final = 8.0
# A Retry-After longer than this is surfaced to the caller instead of slept through.
MAX_HONORED_RETRY_AFTER: Final = 60.0
# The total a single call will sleep on the server's instruction, across every attempt it makes. The
# per-wait cap above bounds one Retry-After; without this, a server repeating a wait just under that
# cap could still hold one method call for max_retries times as long. Only server-directed waits are
# charged to this budget - the SDK's own backoff is bounded by MAX_RETRY_DELAY and stays outside it.
MAX_TOTAL_RETRY_AFTER_WAIT: Final = 60.0

MAX_IMAGE_DIMENSION: Final = 1024
MAX_IMAGE_BYTES: Final = 3_500_000
JPEG_QUALITY_LADDER: Final[tuple[int, ...]] = (85, 75, 65)
# An ICC profile is carried onto a re-encoded JPEG up to this size. sRGB and Display P3 are a few
# kilobytes; a profile far larger than this would spend the byte budget the image itself needs.
MAX_ICC_PROFILE_BYTES: Final = 65_536

ENV_API_KEY: Final = "JANUARY_API_KEY"
ENV_BASE_URL: Final = "JANUARY_BASE_URL"

# What a client token's value starts with. An account key uses "sk-" instead. The API tells the two
# apart itself; the SDK reads the prefix only to decide which local checks apply to a call, never to
# authenticate, so an unrecognized prefix is treated as an account key - the conservative side.
CLIENT_TOKEN_PREFIX: Final = "ct-"
