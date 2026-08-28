"""Turn whatever a caller has on hand into the ``image`` string the scan endpoints accept.

``POST /v1.2/food-scans/photo`` takes a single string: either an http(s) URL that January can fetch
server-side, or a base64 data URI (``data:image/jpeg;base64,...``). Base64 inflates the payload by
about 33% and request bodies over 5 MB are rejected, so encoded images are kept under
``MAX_IMAGE_BYTES``. Accepted formats are JPEG, PNG, WEBP, and non-animated GIF.

:func:`prepare_image` handles the conversion, downscaling, and re-encoding so callers can hand the
SDK a path, raw bytes, an open file, or a Pillow image and get a string the API will accept.

Pillow is imported lazily, inside the handful of functions that need it. Only one of the SDK's
eighteen operations can carry an image at all, and even it skips Pillow entirely for a URL, a
``data:`` URI, or a ``preprocess=False`` JPEG or PNG, so importing it at module scope would load
sixteen modules and several megabytes of codec libraries into every process that merely says
``import january_ai``.
"""

from __future__ import annotations

import base64
import io
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from ._constants import (
    JPEG_QUALITY_LADDER,
    MAX_ICC_PROFILE_BYTES,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_DIMENSION,
)

if TYPE_CHECKING:
    from PIL import Image as PILImageModule

    from ._types import ImageInput

__all__ = ["prepare_image"]

# A str carrying one of these prefixes is already an ``image`` value and is never touched.
_PASS_THROUGH_PREFIXES: Final = ("http://", "https://", "data:")
_LONGEST_PREFIX: Final = max(len(prefix) for prefix in _PASS_THROUGH_PREFIXES)

# A str that is neither a pass-through URI nor a path. The scheme must be at least two characters
# so a Windows drive letter (``C:\photos\lunch.jpg``) is still read as a path, and the ``://``
# authority delimiter is required so a legal POSIX filename such as ``my_photo:final.jpg`` is too.
# ``file:`` is matched on its own because ``file:/etc/passwd`` is valid without an authority.
_UNSUPPORTED_SCHEME: Final = re.compile(r"^(?:file:|[a-zA-Z][a-zA-Z0-9+.-]+://)", re.IGNORECASE)

_FORMAT_MIME: Final[dict[str, str]] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
_ACCEPTED_FORMATS: Final = "JPEG, PNG, WEBP, and non-animated GIF"
# The two accepted formats that can hold more than one frame, so the only two worth opening on the
# pass-through path to count them.
_ANIMATABLE_MIMES: Final = frozenset({"image/gif", "image/webp"})

_ALPHA_MODES: Final = frozenset({"RGBA", "LA", "PA"})
# Premultiplied-alpha modes, mapped to their straight-alpha equivalents. ``RGBa`` converts straight
# to RGB, but ``La`` has no path to ``L`` at all and raises ``conversion from La to L not
# supported`` from inside Pillow; both are un-premultiplied here so the alpha handling below sees a
# mode it knows, and so the two behave the same way as each other.
_PREMULTIPLIED_MODES: Final[dict[str, str]] = {"La": "LA", "RGBa": "RGBA"}
# Single-channel modes wider than 8 bits. ``convert("RGB")`` clips these rather than scaling them,
# turning a 16-bit photo into near-solid white, so they are narrowed by hand first.
#
# Which of them a given file reports is a Pillow version detail, not a property of the file: a
# 16-bit greyscale PNG opens as ``I`` before Pillow 11 and as ``I;16`` from Pillow 11 on. The whole
# set therefore has to travel together everywhere it is consulted.
_WIDE_INT_MODES: Final = frozenset({"I", "I;16", "I;16B", "I;16L", "I;16N"})
# Modes JPEG either cannot store or stores misleadingly, so they always force a re-encode. Every
# wide-integer mode belongs here: one that is missing is judged already-compliant and forwarded
# verbatim, which skips the narrowing above entirely.
_RE_ENCODE_MODES: Final = frozenset({"CMYK", "F"}) | _WIDE_INT_MODES
_WHITE: Final = (255, 255, 255)

# MPF/multi-picture JPEGs - stereo pairs, embedded screennails, some dual-camera captures - report
# more than one frame but are stills, not animations. Pillow's Image.open leaves them on the
# primary frame, which is the photo the caller meant.
_STILL_MULTI_FRAME_FORMATS: Final = frozenset({"MPO"})

# TIFF/EXIF tag 0x0112 (Orientation); 1 means "already upright".
_EXIF_ORIENTATION_TAG: Final = 0x0112
_UPRIGHT_ORIENTATIONS: Final = frozenset({1})

# ISO base media container brands. HEIC is the default iPhone camera format and AVIF is its
# successor; Pillow decodes neither without a plugin, so they are named rather than lumped in with
# genuinely unreadable data.
_BMFF_BRANDS: Final = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}
)

_HEIF_UNSUPPORTED: Final = (
    "HEIC/HEIF/AVIF images (the default iPhone camera format) cannot be decoded by Pillow alone, "
    "and the API accepts only JPEG, PNG, WEBP, and non-animated GIF. Install pillow-heif and call "
    "pillow_heif.register_heif_opener() once at startup, and the SDK will convert the photo for "
    "you; or convert it to JPEG before scanning."
)

_PROCESSING_FAILED: Final = (
    "the image could not be processed; it may be truncated or corrupt. Re-save it as a JPEG or "
    "PNG and try again."
)


def prepare_image(source: ImageInput, *, preprocess: bool = True) -> str:
    """Convert an image into the string the food-scan endpoints expect.

    An ``http``/``https`` URL or an existing ``data:`` URI is returned byte-for-byte unchanged, for
    either value of ``preprocess``; a URL must be publicly fetchable server-side, since January
    downloads it (hosts that block hotlinking or require a login cannot be read). Anything else is
    read into memory and base64-encoded into a ``data:<mime>;base64,...`` string.

    With ``preprocess=True`` (the default) the image is decoded to check it. If it is already a
    JPEG, PNG, WEBP, or non-animated GIF no larger than ``MAX_IMAGE_DIMENSION`` on its longest side
    and ``MAX_IMAGE_BYTES`` on the wire, with no EXIF rotation pending and not in CMYK or a
    high-bit-depth mode, its original bytes are encoded as-is - so a small transparent PNG is
    forwarded as a PNG with its alpha and its metadata intact. Otherwise it is rotated per its EXIF
    orientation, flattened onto white if it has alpha, downscaled to fit ``MAX_IMAGE_DIMENSION``
    (never upscaled, aspect ratio preserved), and saved as JPEG at descending quality until it fits
    the byte budget. Re-encoding is what drops metadata, EXIF GPS coordinates included; passing a
    ``PIL.Image.Image`` always re-encodes, so it always strips. A modest ICC profile on an image
    that is already RGB is the one thing carried across, so a wide-gamut photo does not shift
    colour at the size where downscaling begins.

    With ``preprocess=False`` the bytes are passed through untouched: the format is sniffed from the
    leading magic bytes, the size is checked, and a GIF or WEBP has its frames counted so an
    animation is refused here rather than by the API, but nothing is decoded, rotated, or resized.
    Use it when the image is known to be compliant and the decode cost is not wanted.

    A ``str`` that is neither an http(s) URL nor a ``data:`` URI is read from the local filesystem.
    Never pass one that came from an end user: validate it yourself, or pass ``bytes`` instead.

    Args:
        source: An http(s) URL or ``data:`` URI string, a filesystem path (``str`` or
            ``os.PathLike``), raw ``bytes``/``bytearray``/``memoryview``, a binary file object
            opened for reading, or a ``PIL.Image.Image``.
        preprocess: Whether to decode, downscale, and re-encode the image. A ``PIL.Image.Image``
            has no encoded form of its own and always requires ``True``.

    Returns:
        The unchanged URL or data URI, or a newly built ``data:<mime>;base64,...`` string.

    Raises:
        ValueError: The data is not a decodable image, is animated, is too large to fit the byte
            budget, is a path that exists but cannot be read (a directory, or a file this process
            has no permission for), is a file object that is closed or already at the end of its
            data, carries a URI scheme the SDK does not support, or is a ``PIL.Image.Image``
            combined with ``preprocess=False``.
        TypeError: ``source`` is not one of the accepted types, or is a file object opened in text
            mode rather than binary.
        FileNotFoundError: ``source`` is a path that does not exist.
    """
    if isinstance(source, str):
        if _is_pass_through_uri(source):
            return source
        if _UNSUPPORTED_SCHEME.match(source):
            scheme = source.split(":", 1)[0]
            raise ValueError(
                f"{scheme!r} is not a supported URI scheme; pass an http(s) URL, a data: URI, or "
                f"a filesystem path."
            )
    if _is_pil_image(source):
        if not preprocess:
            raise ValueError(
                "preprocess=False needs raw image bytes, and a PIL.Image.Image has none. "
                "Use preprocess=True, or save the image to a BytesIO yourself and pass those bytes."
            )
        image = _as_pil_image(source)
        _reject_animated(image)
        try:
            return _re_encode(image)
        except OSError as exc:
            raise ValueError(_PROCESSING_FAILED) from exc
    data = _read_source_bytes(source)
    if not preprocess:
        return _encode_verbatim(data)
    return _encode_preprocessed(data)


def _is_pil_image(source: object) -> bool:
    """Report whether the value is a ``PIL.Image.Image``, without importing Pillow to find out.

    A caller holding one necessarily imported Pillow to make it, so ``PIL.Image`` missing from
    ``sys.modules`` is proof this is not one.
    """
    module = sys.modules.get("PIL.Image")
    if module is None:
        return False
    image_class: type[object] = module.Image
    return isinstance(source, image_class)


def _as_pil_image(source: object) -> PILImageModule.Image:
    """Narrow a value :func:`_is_pil_image` has already accepted to Pillow's type."""
    from PIL import Image

    assert isinstance(source, Image.Image)
    return source


def _is_pass_through_uri(source: str) -> bool:
    """Report whether the string is already a usable ``image`` value."""
    # URI schemes are case-insensitive; only the prefix is lowered so huge data URIs stay cheap.
    return source[:_LONGEST_PREFIX].lower().startswith(_PASS_THROUGH_PREFIXES)


def _read_source_bytes(source: object) -> bytes:
    """Pull the raw encoded bytes out of a path, buffer, or binary file object.

    A file object that yields nothing is singled out. ``img.save(buf, "JPEG")`` followed by
    ``prepare_image(buf)`` leaves the buffer positioned at its end, so the read returns zero bytes;
    left to the decoder that becomes "the data is not a readable image", which sends the caller
    looking for a corrupt file instead of the missing ``seek(0)``.
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    if isinstance(source, (str, os.PathLike)):
        try:
            return Path(source).read_bytes()
        except FileNotFoundError:
            # Documented as escaping unwrapped: it names the path and needs no translation.
            raise
        except OSError as exc:
            # IsADirectoryError arrives here too, and its errno text already says as much.
            raise ValueError(
                f"could not read {os.fspath(source)!r} as an image file: {exc}"
            ) from exc
    read = getattr(source, "read", None)
    if callable(read):
        if getattr(source, "closed", False):
            raise ValueError(
                "the file object is already closed, so its bytes cannot be read. Read the image "
                "inside the `with open(...)` block, or pass the path itself instead."
            )
        try:
            chunk = read()
        except UnicodeDecodeError as exc:
            # A text-mode handle chokes on the first non-UTF-8 byte before it can return anything.
            raise TypeError("open the image file in binary mode ('rb')") from exc
        except ValueError as exc:
            # What a closed file raises on a version or file type whose `closed` attribute the
            # check above could not see. Without this the message is a bare "read of closed file".
            raise ValueError(
                f"the file object could not be read ({exc}); it may already be closed. Read the "
                "image before closing it, or pass the path itself instead."
            ) from exc
        if isinstance(chunk, str):
            raise TypeError("open the image file in binary mode ('rb')")
        if isinstance(chunk, (bytes, bytearray, memoryview)):
            data = bytes(chunk)
            if not data:
                raise ValueError(
                    "the file object returned no bytes; it is positioned at the end of its data. "
                    "Call seek(0) on it before passing it - saving into a BytesIO leaves it there."
                )
            return data
        raise TypeError(
            f"reading the file object returned {type(chunk).__name__}, not bytes; "
            "open the image file in binary mode ('rb')"
        )
    raise TypeError(
        f"expected an http(s) URL, a data URI, a path, bytes, a binary file object, or a "
        f"PIL.Image.Image; got {type(source).__name__}"
    )


def _encode_verbatim(data: bytes) -> str:
    """Base64 the bytes exactly as given, after sniffing the format and checking the size.

    ``preprocess=False`` skips decoding, but an animation is not a slow-path detail the API might
    tolerate: it is rejected outright, so passing one through unexamined only moves the failure to
    the far end of a multi-megabyte upload. Frames are counted for the two formats that can hold
    more than one, which reads container headers rather than pixels.
    """
    mime = _sniff_mime(data)
    if mime is None:
        if _is_bmff_image(data):
            # Without this the advice would be "use preprocess=True to convert other formats",
            # which for a HEIC leads only to the decode failing there instead.
            raise ValueError(_HEIF_UNSUPPORTED)
        raise ValueError(
            f"could not recognize the image format from its leading bytes; "
            f"{_ACCEPTED_FORMATS} are accepted. Use preprocess=True to convert other formats."
        )
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"the image is {len(data)} bytes, over the {MAX_IMAGE_BYTES} byte limit for an "
            f"uploaded image. Use preprocess=True to downscale and re-encode it."
        )
    _reject_animated_bytes(data, mime)
    return _data_uri(mime, data)


def _encode_preprocessed(data: bytes) -> str:
    """Decode the image, pass it through untouched when it already complies, else re-encode it."""
    img = _open_image(data)
    _reject_animated(img)
    try:
        if _is_already_compliant(img, len(data)):
            return _data_uri(_FORMAT_MIME[str(img.format)], data)
        return _re_encode(img)
    except OSError as exc:
        raise ValueError(_PROCESSING_FAILED) from exc


def _open_image(data: bytes) -> PILImageModule.Image:
    """Decode the bytes with Pillow, translating its failures into ``ValueError``."""
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except UnidentifiedImageError as exc:
        if _is_bmff_image(data):
            raise ValueError(_HEIF_UNSUPPORTED) from exc
        raise ValueError(
            f"the data is not a readable image; {_ACCEPTED_FORMATS} are accepted. Check that the "
            f"file is a complete image and not HTML, a PDF, or a partial download."
        ) from exc
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        # The warning fires between MAX_IMAGE_PIXELS and twice it, and reaches this handler only in
        # a process that turned warnings into errors. Catching it keeps the module's documented
        # exception set closed either way.
        raise ValueError(
            f"the image has too many pixels to decode safely (Pillow's decompression-bomb guard "
            f"rejected it). Downscale it below {MAX_IMAGE_DIMENSION} pixels on its longest side "
            f"before passing it to the SDK."
        ) from exc
    except OSError as exc:
        raise ValueError(_PROCESSING_FAILED) from exc
    return img


def _is_bmff_image(data: bytes) -> bool:
    """Detect a HEIC/HEIF/AVIF container from the brands in its leading ``ftyp`` box."""
    if data[4:8] != b"ftyp":
        return False
    brands = {data[8:12], *(data[index : index + 4] for index in range(16, min(len(data), 64), 4))}
    return bool(brands & _BMFF_BRANDS)


def _reject_animated(img: PILImageModule.Image) -> None:
    """Refuse multi-frame images, which the scan endpoints do not accept."""
    if img.format in _STILL_MULTI_FRAME_FORMATS:
        return
    frames = getattr(img, "n_frames", 1)
    if isinstance(frames, int) and frames > 1:
        raise ValueError(_animated_message(str(img.format), frames))


def _reject_animated_bytes(data: bytes, mime: str) -> None:
    """Refuse a multi-frame GIF or WEBP on the pass-through path, without decoding its pixels.

    Only the two animatable formats are opened, and only far enough to count frames, so the cost
    ``preprocess=False`` exists to avoid is not reintroduced for the JPEG and PNG cases. Anything
    Pillow cannot parse at all is left alone: the pass-through path promises not to validate the
    image, and the sniffed magic bytes have already had their say.

    Args:
        data: The encoded bytes about to be sent verbatim.
        mime: The MIME type sniffed from those bytes.

    Raises:
        ValueError: If the image holds more than one frame.
    """
    if mime not in _ANIMATABLE_MIMES:
        return
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            image_format = str(img.format)
            counted = getattr(img, "n_frames", 1)
    except Exception:
        # A header this sniff accepted but Pillow will not parse, or a frame count it cannot walk.
        # The pass-through path does not validate images, so nothing here is worth failing over.
        return
    if isinstance(counted, int) and counted > 1:
        raise ValueError(_animated_message(image_format, counted))


def _animated_message(image_format: str, frames: int) -> str:
    """Word the refusal of a multi-frame image identically on both paths."""
    return (
        f"this {image_format} image has {frames} frames; animated images are not supported. "
        f"Pass a single frame instead (for example img.seek(0) followed by "
        f"img.convert('RGB')) or a still photo."
    )


def _is_already_compliant(img: PILImageModule.Image, encoded_size: int) -> bool:
    """Report whether the original bytes can be sent as they are, with no re-encode."""
    return (
        img.format in _FORMAT_MIME
        and max(img.size) <= MAX_IMAGE_DIMENSION
        and encoded_size <= MAX_IMAGE_BYTES
        and _exif_orientation(img) in _UPRIGHT_ORIENTATIONS
        and img.mode not in _RE_ENCODE_MODES
    )


def _exif_orientation(img: PILImageModule.Image) -> int:
    """Return the EXIF orientation tag, defaulting to 1 (upright) when absent or non-integral."""
    value = img.getexif().get(_EXIF_ORIENTATION_TAG)
    return value if isinstance(value, int) else 1


def _re_encode(img: PILImageModule.Image) -> str:
    """Rotate, flatten, downscale, and save the image as a JPEG data URI."""
    from PIL import Image, ImageOps

    # Orientation is expressed against the original axes, so transposing must precede the resize.
    transposed = ImageOps.exif_transpose(img)
    # exif_transpose returns a new image; the copy fallback keeps a caller's image from being
    # mutated by the in-place thumbnail below if a Pillow version ever returns None here.
    prepared = transposed if transposed is not None else img.copy()
    icc_profile = _carried_icc_profile(img)
    prepared = _flatten(prepared)
    # thumbnail preserves the aspect ratio, mutates in place, and never upscales.
    prepared.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)

    for quality in JPEG_QUALITY_LADDER:
        buffer = io.BytesIO()
        # No exif= argument: re-encoding is what strips metadata, GPS coordinates included. The ICC
        # profile is the exception - see _carried_icc_profile.
        prepared.save(
            buffer, format="JPEG", quality=quality, optimize=True, icc_profile=icc_profile
        )
        encoded = buffer.getvalue()
        if len(encoded) <= MAX_IMAGE_BYTES:
            return _data_uri("image/jpeg", encoded)
    raise ValueError(
        f"the image still exceeds {MAX_IMAGE_BYTES} bytes at JPEG quality "
        f"{JPEG_QUALITY_LADDER[-1]}. Crop it or downscale it further before scanning."
    )


def _carried_icc_profile(img: PILImageModule.Image) -> bytes | None:
    """Return the ICC profile to attach to the re-encoded JPEG, or ``None`` to attach none.

    An image that already passes through untouched keeps its profile, so dropping it on the
    re-encode makes a wide-gamut photo shift colour at exactly one threshold - the pixel where
    downscaling starts. Carrying it across removes that discontinuity.

    Two guards keep this safe rather than merely faithful. The profile describes the colour space
    of the pixels it shipped with, so it is carried only when those pixels are already RGB and the
    re-encode will therefore not convert between colour spaces; a CMYK profile attached to the RGB
    JPEG this function produces would misrepresent every pixel, which is worse than no profile at
    all. And an unusually large profile is dropped, since it would spend the byte budget the image
    itself needs - the common sRGB and Display P3 profiles are a few kilobytes at most.
    """
    if img.mode != "RGB":
        return None
    profile = img.info.get("icc_profile")
    if not isinstance(profile, bytes) or len(profile) > MAX_ICC_PROFILE_BYTES:
        return None
    return profile


def _flatten(img: PILImageModule.Image) -> PILImageModule.Image:
    """Return the image in RGB mode, compositing any transparency onto a white background."""
    from PIL import Image

    straight = _PREMULTIPLIED_MODES.get(img.mode)
    if straight is not None:
        img = img.convert(straight)
    if img.mode == "P" and "transparency" in img.info:
        # Palette transparency is lost going straight to RGB, turning clear pixels black.
        img = img.convert("RGBA")
    if img.mode in _ALPHA_MODES:
        background = Image.new("RGB", img.size, _WHITE)
        background.paste(img, mask=img.split()[-1])
        return background
    if img.mode in _WIDE_INT_MODES:
        # convert("RGB") clips at 255 instead of rescaling, so a 16-bit scan would arrive as
        # near-solid white. Shift the fixed 16-bit range down to 8 bits first.
        img = img.convert("I").point(lambda value: value * (1 / 256)).convert("L")
    elif img.mode == "F":
        # A float image has no defined range, so normalize it against its own extrema. F is a
        # single-band mode, so getextrema returns one (low, high) pair rather than one per band.
        low, high = cast("tuple[float, float]", img.getextrema())
        scale = 255.0 / (high - low) if high > low else 0.0
        img = img.point(lambda value: (value - low) * scale).convert("L")
    if img.mode != "RGB":
        try:
            return img.convert("RGB")
        except ValueError as exc:
            # Pillow refuses some mode pairs outright, and says so with a bare message carrying no
            # SDK frame. Naming the mode gives the caller something to act on for any mode this
            # module has not met yet.
            raise ValueError(
                f"the image is in Pillow mode {img.mode!r}, which cannot be converted to RGB for "
                f"encoding. Convert it yourself before scanning, or save it as a JPEG or PNG first."
            ) from exc
    return img


def _sniff_mime(data: bytes) -> str | None:
    """Identify JPEG, PNG, GIF, or WEBP from the file's magic bytes."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _data_uri(mime: str, payload: bytes) -> str:
    """Build a ``data:<mime>;base64,...`` string around the payload."""
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
