"""Shared fixtures for the whole suite: instrumented clients, a respx router, and test images.

Three rules hold everywhere below, because breaking any of them makes the suite either slow or
untrustworthy.

Nothing sleeps. Both clients get their ``_sleep`` replaced by a recorder that appends the requested
delay to a list and returns at once, so a test that exercises three retries costs microseconds and
can still assert exactly how long the SDK intended to wait. Their ``_rng`` is seeded with
``random.Random(0)`` so the jitter in :func:`january_ai._backoff.compute_delay` is reproducible.

Nothing reaches the network. Requests are intercepted by respx, routed at
``https://partners.january.ai`` - the SDK's own default origin, so the fixtures also confirm that
the client builds the URLs it claims to.

No binary files live in the repository. Every image fixture is drawn with Pillow when the module
that needs it first asks, which keeps the checkout text-only and makes each image's exact
properties - its size, its mode, its EXIF orientation, which corner is transparent - readable right
here rather than hidden inside a blob.
"""

from __future__ import annotations

import io
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from pathlib import Path
from typing import Final, Protocol

import httpx
import pytest
import respx
from PIL import Image, ImageDraw, ImageOps

from january_ai import AsyncJanuary, January
from january_ai._base_client import AsyncAPIClient, SyncAPIClient
from january_ai._constants import DEFAULT_MAX_RETRIES

BASE_URL: Final = "https://partners.january.ai"
"""The origin every route in the suite is registered under: the SDK's own default."""

API_PREFIX: Final = "/v1.2"
"""The version prefix every operation path carries."""

TEST_API_KEY: Final = "sk-test"
"""The credential the fixture clients authenticate with."""

EXIF_ORIENTATION_TAG: Final = 0x0112
"""TIFF/EXIF tag holding the orientation an image should be rotated by before display."""

LEFT_COLOR: Final = (220, 20, 20)
"""Colour of the left half of the EXIF-rotated fixture, before rotation."""

RIGHT_COLOR: Final = (20, 20, 220)
"""Colour of the right half of the EXIF-rotated fixture, before rotation."""

RGBA_OPAQUE_COLOR: Final = (30, 120, 200, 255)
"""Colour of the opaque region of the RGBA fixture."""

RGBA_CLEAR_CORNER: Final = (0, 0, 100, 100)
"""Bounding box, inclusive of both corners, of the fully transparent region of the RGBA fixture."""

GIF_OPAQUE_COLOR: Final = (220, 40, 40)
"""Palette entry 0 of the transparent-GIF fixture: the colour of its opaque region."""

GIF_TRANSPARENT_INDEX: Final = 1
"""Palette entry 1 of the transparent-GIF fixture, which is black and marked transparent.

Black on purpose: a flatten that drops palette transparency instead of compositing it turns the
clear region black, and a black-on-black failure would be invisible against any other palette.
"""


def api_url(path: str) -> str:
    """Build the absolute URL of an API path under the test origin.

    Args:
        path: A path including its ``/v1.2`` prefix, such as ``/v1.2/credits``.

    Returns:
        The absolute URL respx routes and the SDK requests.
    """
    return f"{BASE_URL}{path}"


# --------------------------------------------------------------------------------------------
# Clocks
# --------------------------------------------------------------------------------------------


def sync_sleeper(recorded: list[float]) -> Callable[[float], None]:
    """Build a stand-in for ``time.sleep`` that records the delay and returns immediately.

    Args:
        recorded: The list every requested delay is appended to, in order.

    Returns:
        A callable suitable for :attr:`january_ai._base_client.SyncAPIClient._sleep`.
    """

    def _sleep(delay: float) -> None:
        recorded.append(delay)

    return _sleep


def async_sleeper(recorded: list[float]) -> Callable[[float], Awaitable[None]]:
    """Build a stand-in for ``anyio.sleep`` that records the delay and returns immediately.

    Args:
        recorded: The list every requested delay is appended to, in order.

    Returns:
        A coroutine function suitable for :attr:`january_ai._base_client.AsyncAPIClient._sleep`.
    """

    async def _sleep(delay: float) -> None:
        recorded.append(delay)

    return _sleep


def instrument_sync(api: SyncAPIClient, recorded: list[float]) -> None:
    """Make a transport deterministic: no real sleeping, no unseeded jitter.

    Args:
        api: The transport underneath a :class:`january_ai.January`.
        recorded: The list retry delays are appended to.
    """
    api._sleep = sync_sleeper(recorded)
    api._rng = random.Random(0)


def instrument_async(api: AsyncAPIClient, recorded: list[float]) -> None:
    """Make an async transport deterministic: no real sleeping, no unseeded jitter.

    Args:
        api: The transport underneath a :class:`january_ai.AsyncJanuary`.
        recorded: The list retry delays are appended to.
    """
    api._sleep = async_sleeper(recorded)
    api._rng = random.Random(0)


@pytest.fixture
def recorded_sleeps() -> list[float]:
    """Collect, in order, every delay the clients under test asked to wait.

    Empty after a request that succeeded first time; one entry per retry otherwise.
    """
    return []


@pytest.fixture
def fake_sleep(recorded_sleeps: list[float]) -> Callable[[float], None]:
    """A synchronous sleeper that records its delay into :func:`recorded_sleeps` and returns."""
    return sync_sleeper(recorded_sleeps)


@pytest.fixture
def fake_async_sleep(recorded_sleeps: list[float]) -> Callable[[float], Awaitable[None]]:
    """An async sleeper that records its delay into :func:`recorded_sleeps` and returns."""
    return async_sleeper(recorded_sleeps)


# --------------------------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------------------------


class ClientFactory(Protocol):
    """Builds instrumented :class:`january_ai.January` clients with the constructor's arguments."""

    def __call__(
        self,
        *,
        api_key: str | None = ...,
        base_url: str | httpx.URL | None = ...,
        timeout: float | httpx.Timeout | None = ...,
        max_retries: int = ...,
        default_end_user_id: str | None = ...,
        default_headers: Mapping[str, str] | None = ...,
        http_client: httpx.Client | None = ...,
    ) -> January:
        """Build a client, defaulting ``api_key`` to the suite's test credential."""
        ...


class AsyncClientFactory(Protocol):
    """Builds instrumented :class:`january_ai.AsyncJanuary` clients the same way."""

    def __call__(
        self,
        *,
        api_key: str | None = ...,
        base_url: str | httpx.URL | None = ...,
        timeout: float | httpx.Timeout | None = ...,
        max_retries: int = ...,
        default_end_user_id: str | None = ...,
        default_headers: Mapping[str, str] | None = ...,
        http_client: httpx.AsyncClient | None = ...,
    ) -> AsyncJanuary:
        """Build a client, defaulting ``api_key`` to the suite's test credential."""
        ...


@pytest.fixture
def make_client(recorded_sleeps: list[float]) -> Iterator[ClientFactory]:
    """Yield a factory for variant synchronous clients, closing every one it built.

    Use it wherever a test needs configuration the shared :func:`client` fixture does not have -
    a different retry budget, a pinned header, a caller-owned ``httpx.Client``. Each client is
    instrumented exactly as :func:`client` is, so retry delays land in the same
    :func:`recorded_sleeps` list.
    """
    created: list[January] = []

    def factory(
        *,
        api_key: str | None = TEST_API_KEY,
        base_url: str | httpx.URL | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_end_user_id: str | None = None,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.Client | None = None,
    ) -> January:
        client = January(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_end_user_id=default_end_user_id,
            default_headers=default_headers,
            http_client=http_client,
        )
        instrument_sync(client._client, recorded_sleeps)
        created.append(client)
        return client

    yield factory
    for client in created:
        client.close()


@pytest.fixture
async def make_async_client(recorded_sleeps: list[float]) -> AsyncIterator[AsyncClientFactory]:
    """Yield a factory for variant asynchronous clients, closing every one it built."""
    created: list[AsyncJanuary] = []

    def factory(
        *,
        api_key: str | None = TEST_API_KEY,
        base_url: str | httpx.URL | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_end_user_id: str | None = None,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> AsyncJanuary:
        client = AsyncJanuary(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_end_user_id=default_end_user_id,
            default_headers=default_headers,
            http_client=http_client,
        )
        instrument_async(client._client, recorded_sleeps)
        created.append(client)
        return client

    yield factory
    for client in created:
        await client.aclose()


@pytest.fixture
def client(make_client: ClientFactory) -> Iterator[January]:
    """A synchronous client on the default configuration, with sleeping and jitter pinned."""
    instance = make_client()
    yield instance
    instance.close()


@pytest.fixture
async def async_client(make_async_client: AsyncClientFactory) -> AsyncIterator[AsyncJanuary]:
    """An asynchronous client on the default configuration, with sleeping and jitter pinned."""
    instance = make_async_client()
    yield instance
    await instance.aclose()


# --------------------------------------------------------------------------------------------
# Transport and routing
# --------------------------------------------------------------------------------------------


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    """Run every ``@pytest.mark.anyio`` test twice, once per supported event loop.

    The SDK waits with ``anyio.sleep`` rather than ``asyncio.sleep`` precisely so it runs under
    trio, and a claim like that is only worth making if the suite proves it on both.
    """
    backend = request.param
    assert isinstance(backend, str)
    return backend


@pytest.fixture(scope="session")
def base_url() -> str:
    """The origin the suite routes and asserts against."""
    return BASE_URL


@pytest.fixture
def respx_mock() -> Iterator[respx.MockRouter]:
    """A respx router based at the API origin, so routes are registered by path alone.

    Unused routes are not an error: a test that registers a route per retry attempt and then
    asserts the request was never replayed is a legitimate shape, and failing it for the unused
    route would test the fixture rather than the SDK.
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


# --------------------------------------------------------------------------------------------
# Image builders
# --------------------------------------------------------------------------------------------


def _encode(image: Image.Image, image_format: str, **options: object) -> bytes:
    """Save a Pillow image to memory and return the encoded bytes."""
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **options)
    return buffer.getvalue()


def build_gradient_jpeg(size: tuple[int, int], *, quality: int = 90) -> bytes:
    """Encode a smooth three-channel gradient as a baseline RGB JPEG with no EXIF.

    Args:
        size: Width and height in pixels.
        quality: The JPEG quality to save at.

    Returns:
        The encoded JPEG bytes.
    """
    ramp = Image.linear_gradient("L").resize(size, Image.Resampling.BILINEAR)
    image = Image.merge(
        "RGB",
        (ramp, ImageOps.invert(ramp), ramp.transpose(Image.Transpose.FLIP_LEFT_RIGHT)),
    )
    return _encode(image, "JPEG", quality=quality)


def build_noise_jpeg(size: tuple[int, int], *, seed: int, quality: int = 95) -> bytes:
    """Encode deterministic pixel noise as a JPEG.

    Noise is incompressible, which is the point: it produces a file large enough, and a quality
    ladder separated enough, to test the byte budget without needing a megapixel image.

    Args:
        size: Width and height in pixels.
        seed: Seed for the pixel data, so the bytes are identical on every run.
        quality: The JPEG quality to save at.

    Returns:
        The encoded JPEG bytes.
    """
    width, height = size
    noise = random.Random(seed).randbytes(width * height * 3)
    return _encode(Image.frombytes("RGB", size, noise), "JPEG", quality=quality)


def build_oriented_jpeg(orientation: int, size: tuple[int, int] = (400, 200)) -> bytes:
    """Encode a left/right two-colour JPEG carrying an EXIF orientation tag.

    The halves are split along the vertical axis and the image is wider than it is tall, so
    applying the orientation changes both the dimensions and which half is where. A test can
    therefore tell a real rotation from a resize that merely swapped the reported size.

    Args:
        orientation: The EXIF orientation value to record, 1 through 8.
        size: Width and height in pixels.

    Returns:
        The encoded JPEG bytes.
    """
    width, height = size
    image = Image.new("RGB", size, LEFT_COLOR)
    ImageDraw.Draw(image).rectangle((width // 2, 0, width - 1, height - 1), fill=RIGHT_COLOR)
    exif = image.getexif()
    exif[EXIF_ORIENTATION_TAG] = orientation
    return _encode(image, "JPEG", quality=95, exif=exif)


def build_rgba_png(size: tuple[int, int] = (500, 300)) -> bytes:
    """Encode an RGBA PNG whose top-left corner is fully transparent.

    The transparent pixels carry a saturated green underneath, which a correct flatten replaces
    with white and an incorrect one leaves showing.

    Args:
        size: Width and height in pixels.

    Returns:
        The encoded PNG bytes.
    """
    image = Image.new("RGBA", size, RGBA_OPAQUE_COLOR)
    ImageDraw.Draw(image).rectangle(RGBA_CLEAR_CORNER, fill=(10, 200, 10, 0))
    return _encode(image, "PNG")


def build_transparent_gif(size: tuple[int, int] = (1200, 800)) -> bytes:
    """Encode a palette GIF whose top-left quadrant is transparent.

    Deliberately larger than ``MAX_IMAGE_DIMENSION``: a compliant GIF is passed through untouched,
    so only an oversized one reaches the palette-transparency handling this fixture exists to test.

    Args:
        size: Width and height in pixels.

    Returns:
        The encoded GIF bytes.
    """
    width, height = size
    image = Image.new("P", size, 0)
    image.putpalette(list(GIF_OPAQUE_COLOR) + [0, 0, 0] * 255)
    ImageDraw.Draw(image).rectangle(
        (0, 0, width // 2 - 1, height // 2 - 1), fill=GIF_TRANSPARENT_INDEX
    )
    return _encode(image, "GIF", transparency=GIF_TRANSPARENT_INDEX)


def build_animated_gif(size: tuple[int, int] = (48, 48)) -> bytes:
    """Encode a two-frame animated GIF.

    Args:
        size: Width and height in pixels.

    Returns:
        The encoded GIF bytes.
    """
    palette = [255, 0, 0, 0, 0, 255] + [0, 0, 0] * 254
    first = Image.new("P", size, 0)
    first.putpalette(palette)
    second = Image.new("P", size, 1)
    second.putpalette(palette)
    buffer = io.BytesIO()
    first.save(buffer, format="GIF", save_all=True, append_images=[second], duration=120, loop=0)
    return buffer.getvalue()


def build_animated_webp(size: tuple[int, int] = (48, 48)) -> bytes:
    """Encode a two-frame animated WEBP.

    The other format the API accepts that can hold an animation, and the other one the
    ``preprocess=False`` path therefore has to count frames for.

    Args:
        size: Width and height in pixels.

    Returns:
        The encoded WEBP bytes.
    """
    first = Image.new("RGB", size, (220, 40, 40))
    second = Image.new("RGB", size, (40, 40, 220))
    buffer = io.BytesIO()
    first.save(buffer, format="WEBP", save_all=True, append_images=[second], duration=120, loop=0)
    return buffer.getvalue()


def build_icc_jpeg(size: tuple[int, int], *, quality: int = 90) -> tuple[bytes, bytes]:
    """Encode a JPEG carrying an embedded sRGB ICC profile.

    Args:
        size: Width and height in pixels.
        quality: The JPEG quality to save at.

    Returns:
        A ``(encoded_bytes, icc_profile)`` pair, so a test can compare what came back against the
        exact profile that went in rather than merely against "something".
    """
    from PIL import ImageCms

    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    ramp = Image.linear_gradient("L").resize(size, Image.Resampling.BILINEAR)
    image = Image.merge("RGB", (ramp, ImageOps.invert(ramp), ramp))
    return _encode(image, "JPEG", quality=quality, icc_profile=profile), profile


def build_cmyk_jpeg(size: tuple[int, int] = (240, 160)) -> bytes:
    """Encode a JPEG in CMYK, the colour space a scanner or a print workflow produces.

    Args:
        size: Width and height in pixels.

    Returns:
        The encoded JPEG bytes.
    """
    return _encode(Image.new("CMYK", size, (200, 40, 10, 5)), "JPEG", quality=92)


def build_16bit_png(size: tuple[int, int] = (120, 80)) -> bytes:
    """Encode a 16-bit greyscale PNG, which Pillow opens in mode ``I;16``.

    Args:
        size: Width and height in pixels.

    Returns:
        The encoded PNG bytes.
    """
    return _encode(Image.new("I;16", size, 30000), "PNG")


def build_16bit_ramp_png(size: tuple[int, int] = (256, 64)) -> bytes:
    """Encode a 16-bit greyscale PNG sweeping the full range, so clipping is visible.

    A flat 16-bit image only shows that *a* value was mishandled. A ramp shows how: narrowing by
    clipping collapses everything above 255 to white, leaving three distinct greys out of 256,
    while narrowing by scaling reproduces the ramp.

    Args:
        size: Width and height in pixels. Width sets how many ramp steps there are.

    Returns:
        The encoded PNG bytes.
    """
    width, height = size
    image = Image.new("I;16", size)
    image.putdata([x * 257 for _ in range(height) for x in range(width)])
    return _encode(image, "PNG")


def build_float_tiff(size: tuple[int, int] = (256, 64)) -> bytes:
    """Encode a 32-bit float greyscale TIFF ramp over 0..1, which Pillow opens in mode ``F``.

    Args:
        size: Width and height in pixels.

    Returns:
        The encoded TIFF bytes.
    """
    width, height = size
    image = Image.new("F", size)
    image.putdata([x / (width - 1) for _ in range(height) for x in range(width)])
    return _encode(image, "TIFF")


def build_multi_picture_jpeg(
    primary: tuple[int, int] = (900, 1200), secondary: tuple[int, int] = (320, 240)
) -> bytes:
    """Encode an MPF multi-picture JPEG: an ordinary still carrying a second embedded image.

    Stereo cameras, some dual-camera phones, and compacts that embed a screennail all produce
    these. Pillow promotes them to ``MpoImageFile`` and reports ``n_frames == 2``, which is why a
    frame count alone cannot stand in for "animated". The two frames differ in aspect ratio so a
    test can prove which one was sent.

    Args:
        primary: Width and height of the photo itself.
        secondary: Width and height of the embedded second image.

    Returns:
        The encoded MPO bytes, which begin with the JPEG magic and are a JPEG on the wire.
    """
    first = build_noise_jpeg(primary, seed=11)
    second = build_noise_jpeg(secondary, seed=12)
    buffer = io.BytesIO()
    Image.open(io.BytesIO(first)).save(
        buffer, format="MPO", append_images=[Image.open(io.BytesIO(second))]
    )
    return buffer.getvalue()


def build_heic_header() -> bytes:
    """Return an ISO-BMFF ``ftyp`` box declaring the HEIC brand, padded to a plausible length.

    Only the container header matters: the SDK sniffs the brand before handing anything to Pillow,
    which cannot decode HEIC at all without a plugin, so a full HEIC fixture would prove nothing
    extra and would need a codec the test environment may not have.

    Returns:
        Bytes that any HEIC sniff recognizes and no image decoder can read.
    """
    return b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + bytes(256)


def build_truncated_jpeg() -> bytes:
    """Return the leading third of a valid JPEG: a plausible partial download.

    The header parses, so the failure surfaces during decoding rather than at open time - the
    case a naive format sniff would wave through.
    """
    data = build_noise_jpeg((600, 400), seed=99)
    return data[: len(data) // 3]


# --------------------------------------------------------------------------------------------
# Image fixtures
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_jpeg_bytes() -> bytes:
    """A JPEG that already complies: RGB, 200x150, no EXIF, far under the byte budget."""
    return build_gradient_jpeg((200, 150))


@pytest.fixture(scope="module")
def small_jpeg_path(tmp_path_factory: pytest.TempPathFactory, small_jpeg_bytes: bytes) -> Path:
    """The compliant JPEG written to a temporary file, for the path-input cases."""
    path = tmp_path_factory.mktemp("images") / "meal.jpg"
    path.write_bytes(small_jpeg_bytes)
    return path


@pytest.fixture(scope="module")
def oversized_jpeg_bytes() -> bytes:
    """A 2000x1500 RGB JPEG: over the dimension limit, with a 4:3 ratio to check downscaling."""
    return build_gradient_jpeg((2000, 1500))


@pytest.fixture(scope="module")
def noisy_jpeg_bytes() -> bytes:
    """A 1200x900 JPEG of pixel noise, large enough to walk the JPEG quality ladder."""
    return build_noise_jpeg((1200, 900), seed=1234)


@pytest.fixture(scope="module")
def exif_rotated_jpeg_bytes() -> bytes:
    """A 400x200 JPEG tagged EXIF orientation 6: red left half, blue right half, unrotated."""
    return build_oriented_jpeg(6)


@pytest.fixture(scope="module")
def rgba_png_bytes() -> bytes:
    """A 500x300 RGBA PNG with a fully transparent 100x100 top-left corner."""
    return build_rgba_png()


@pytest.fixture(scope="module")
def transparent_gif_bytes() -> bytes:
    """A 1200x800 palette GIF whose top-left quadrant uses a transparent black palette entry."""
    return build_transparent_gif()


@pytest.fixture(scope="module")
def animated_gif_bytes() -> bytes:
    """A two-frame animated GIF, which the scan endpoints do not accept."""
    return build_animated_gif()


@pytest.fixture(scope="module")
def animated_webp_bytes() -> bytes:
    """A two-frame animated WEBP: the other format that can carry an animation."""
    return build_animated_webp()


@pytest.fixture(scope="module")
def cmyk_jpeg_bytes() -> bytes:
    """A CMYK JPEG, which has to be converted before it can be sent."""
    return build_cmyk_jpeg()


@pytest.fixture(scope="module")
def sixteen_bit_png_bytes() -> bytes:
    """A 16-bit greyscale PNG, opened by Pillow in mode ``I;16``."""
    return build_16bit_png()


@pytest.fixture(scope="module")
def sixteen_bit_ramp_png_bytes() -> bytes:
    """A 16-bit greyscale PNG sweeping the full range, so a clipped narrowing is visible."""
    return build_16bit_ramp_png()


@pytest.fixture(scope="module")
def float_tiff_bytes() -> bytes:
    """A 32-bit float greyscale TIFF ramp, opened by Pillow in mode ``F``."""
    return build_float_tiff()


@pytest.fixture(scope="module")
def multi_picture_jpeg_bytes() -> bytes:
    """An MPF multi-picture JPEG: a still photo that reports two frames."""
    return build_multi_picture_jpeg()


@pytest.fixture(scope="module")
def heic_header_bytes() -> bytes:
    """An ISO-BMFF header declaring the HEIC brand, which Pillow alone cannot decode."""
    return build_heic_header()


@pytest.fixture(scope="module")
def truncated_jpeg_bytes() -> bytes:
    """The leading third of a JPEG: a header that parses over data that cannot be decoded."""
    return build_truncated_jpeg()
