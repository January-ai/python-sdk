"""Tests for :func:`january_ai.prepare_image`.

The function sits between a caller's messy input and a request body the API will accept, so the
assertions here are about pixels and bytes rather than about return types: that a rotation actually
moved the red half of the picture, that a transparent corner came out white and not black, that a
compliant JPEG was forwarded byte for byte rather than quietly re-encoded, and that every rejection
names something the caller can act on.
"""

from __future__ import annotations

import base64
import io
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from PIL import Image, ImageOps

from january_ai import _images as images_module
from january_ai import prepare_image
from january_ai._constants import JPEG_QUALITY_LADDER, MAX_IMAGE_DIMENSION

from .conftest import (
    LEFT_COLOR,
    RIGHT_COLOR,
    build_gradient_jpeg,
    build_icc_jpeg,
    build_oriented_jpeg,
    build_transparent_gif,
)

PASS_THROUGH_URIS = [
    "https://cdn.example.com/meals/lunch.jpg?sig=abc%20def",
    "http://example.com/a.png",
    "data:image/png;base64,iVBORw0KGgo=",
]


def decode_data_uri(value: str) -> tuple[str, bytes]:
    """Split a ``data:<mime>;base64,<payload>`` string into its media type and its bytes."""
    assert value.startswith("data:"), value[:64]
    header, separator, payload = value.partition(",")
    assert separator == ","
    assert header.endswith(";base64"), header
    return header[len("data:") : -len(";base64")], base64.b64decode(payload)


def open_result(value: str) -> Image.Image:
    """Decode the image a prepared data URI carries."""
    _, data = decode_data_uri(value)
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def pixel(image: Image.Image, xy: tuple[int, int]) -> tuple[int, ...]:
    """Read one pixel as a channel tuple."""
    value = image.getpixel(xy)
    assert isinstance(value, tuple)
    return value


def assert_close(actual: tuple[int, ...], expected: tuple[int, ...], *, tolerance: int = 8) -> None:
    """Assert two colours match within a tolerance, since JPEG is lossy."""
    assert len(actual) == len(expected)
    for channel, (got, want) in enumerate(zip(actual, expected, strict=True)):
        assert abs(got - want) <= tolerance, f"channel {channel}: {actual} vs {expected}"


# --------------------------------------------------------------------------------------------
# Pass-through
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("uri", PASS_THROUGH_URIS)
@pytest.mark.parametrize("preprocess", [True, False])
def test_uri_is_returned_unchanged(uri: str, preprocess: bool) -> None:
    """A URL or data URI is forwarded verbatim, whatever ``preprocess`` says."""
    result = prepare_image(uri, preprocess=preprocess)

    assert result == uri
    assert result is uri


def test_uri_scheme_match_is_case_insensitive() -> None:
    """URI schemes are case-insensitive, so an uppercase one is still a pass-through."""
    uri = "HTTPS://cdn.example.com/meal.jpg"

    assert prepare_image(uri) is uri


def test_pass_through_precedes_any_filesystem_access() -> None:
    """A URL is recognized before the path branch, so no lookup of a same-named file happens."""
    uri = "https://example.com/definitely/not/a/local/path.jpg"

    assert prepare_image(uri) == uri


# --------------------------------------------------------------------------------------------
# Fast path
# --------------------------------------------------------------------------------------------


def test_compliant_jpeg_is_forwarded_byte_for_byte(small_jpeg_bytes: bytes) -> None:
    """An RGB JPEG under both limits with no EXIF rotation is encoded, never re-compressed."""
    mime, decoded = decode_data_uri(prepare_image(small_jpeg_bytes))

    assert mime == "image/jpeg"
    assert decoded == small_jpeg_bytes


def test_compliant_rgba_png_keeps_its_alpha(rgba_png_bytes: bytes) -> None:
    """A compliant PNG is forwarded as a PNG: nothing is flattened that need not be."""
    mime, decoded = decode_data_uri(prepare_image(rgba_png_bytes))

    assert mime == "image/png"
    assert decoded == rgba_png_bytes
    assert open_result(prepare_image(rgba_png_bytes)).mode == "RGBA"


# --------------------------------------------------------------------------------------------
# Resizing
# --------------------------------------------------------------------------------------------


def test_oversized_image_is_downscaled_preserving_aspect_ratio(oversized_jpeg_bytes: bytes) -> None:
    """A 2000x1500 photo comes back at 1024x768: longest side capped, 4:3 ratio intact."""
    source = Image.open(io.BytesIO(oversized_jpeg_bytes))

    result = open_result(prepare_image(oversized_jpeg_bytes))

    assert source.size == (2000, 1500)
    assert max(result.size) == MAX_IMAGE_DIMENSION
    assert result.size == (1024, 768)
    assert result.width / result.height == pytest.approx(source.width / source.height, abs=0.005)


def test_smaller_image_is_never_upscaled(rgba_png_bytes: bytes) -> None:
    """A 500x300 image stays 500x300: the resize is a ceiling, not a target."""
    source = Image.open(io.BytesIO(rgba_png_bytes))

    # A PIL image is always re-encoded, which is what puts the resize step in the path at all.
    result = open_result(prepare_image(source))

    assert result.size == (500, 300)


# --------------------------------------------------------------------------------------------
# Orientation, transparency, colour space
# --------------------------------------------------------------------------------------------


def test_exif_orientation_is_applied(exif_rotated_jpeg_bytes: bytes) -> None:
    """Orientation 6 rotates the picture a quarter turn clockwise, moving pixels, not just size."""
    stored = Image.open(io.BytesIO(exif_rotated_jpeg_bytes))
    assert stored.size == (400, 200)
    assert_close(pixel(stored, (100, 100)), LEFT_COLOR)

    result = open_result(prepare_image(exif_rotated_jpeg_bytes))

    # The stored left half becomes the displayed top half, and the frame turns portrait.
    assert result.size == (200, 400)
    assert_close(pixel(result, (100, 60)), LEFT_COLOR)
    assert_close(pixel(result, (100, 340)), RIGHT_COLOR)


def test_upright_orientation_tag_still_takes_the_fast_path() -> None:
    """Orientation 1 means nothing to rotate, so the original bytes are forwarded."""
    data = build_oriented_jpeg(1)

    _, decoded = decode_data_uri(prepare_image(data))

    assert decoded == data


def test_re_encoding_strips_exif(exif_rotated_jpeg_bytes: bytes) -> None:
    """Metadata, GPS coordinates included, does not survive the re-encode."""
    result = open_result(prepare_image(exif_rotated_jpeg_bytes))

    assert dict(result.getexif()) == {}


def test_rgba_transparency_is_flattened_onto_white(rgba_png_bytes: bytes) -> None:
    """A clear corner composites onto white, not onto the colour hiding underneath it."""
    source = Image.open(io.BytesIO(rgba_png_bytes))

    result = open_result(prepare_image(source))

    assert result.mode == "RGB"
    assert_close(pixel(result, (5, 5)), (255, 255, 255))
    assert_close(pixel(result, (400, 250)), (30, 120, 200))


def test_transparent_palette_gif_does_not_turn_black(transparent_gif_bytes: bytes) -> None:
    """Palette transparency is honoured: the clear quadrant is white, not its palette colour."""
    source = Image.open(io.BytesIO(transparent_gif_bytes))
    assert source.mode == "P"
    assert source.info["transparency"] == 1

    result = open_result(prepare_image(transparent_gif_bytes))

    assert result.mode == "RGB"
    assert max(result.size) == MAX_IMAGE_DIMENSION
    assert_close(pixel(result, (20, 20)), (255, 255, 255), tolerance=12)


def test_cmyk_is_converted_to_rgb(cmyk_jpeg_bytes: bytes) -> None:
    """A CMYK JPEG is re-encoded even though it is small, because JPEG can carry it misleadingly."""
    assert Image.open(io.BytesIO(cmyk_jpeg_bytes)).mode == "CMYK"

    prepared = prepare_image(cmyk_jpeg_bytes)
    mime, decoded = decode_data_uri(prepared)

    assert mime == "image/jpeg"
    assert decoded != cmyk_jpeg_bytes
    assert open_result(prepared).mode == "RGB"


def test_16_bit_grayscale_is_converted_to_rgb(sixteen_bit_png_bytes: bytes) -> None:
    """A 16-bit greyscale PNG is narrowed to 8-bit RGB rather than forwarded."""
    # Which wide-integer mode name Pillow reports for this file is a version detail, not a property
    # of the file: "I" before Pillow 11, "I;16" from Pillow 11 on. Both must be narrowed, and for a
    # while only one of them was - the other was judged already-compliant and forwarded verbatim.
    assert Image.open(io.BytesIO(sixteen_bit_png_bytes)).mode in {"I", "I;16"}

    prepared = prepare_image(sixteen_bit_png_bytes)

    assert decode_data_uri(prepared)[0] == "image/jpeg"
    assert open_result(prepared).mode == "RGB"
    # Asserting the mode alone let a total corruption through: convert("RGB") *clips* a wide
    # single-channel mode instead of scaling it, so this flat 30000 arrived as pure white. The
    # 8-bit value that actually corresponds to 30000 is 30000 // 256 == 117.
    assert_close(pixel(open_result(prepared), (0, 0)), (117, 117, 117))


def test_16_bit_grayscale_ramp_keeps_its_tones(sixteen_bit_ramp_png_bytes: bytes) -> None:
    """Narrow a 16-bit image by rescaling it, not by clipping everything above 255 to white."""
    # The flat fixture above proves one value is wrong; the ramp proves the shape of the damage.
    # Clipping leaves three distinct greys out of 256 and a 99% white image, which is not a photo
    # any nutrition model can read - and nothing warns, because the SDK returns a perfectly
    # well-formed data URI either way.
    prepared = open_result(prepare_image(sixteen_bit_ramp_png_bytes))

    sampled = [pixel(prepared, (x, 0))[0] for x in (0, 32, 64, 128, 192, 255)]
    assert_close(tuple(sampled), (0, 32, 64, 128, 192, 255))
    assert len({prepared.getpixel((x, 0)) for x in range(256)}) > 200


def test_float_mode_image_is_normalized_rather_than_clipped(float_tiff_bytes: bytes) -> None:
    """Scale a float image against its own extrema, since the mode defines no range."""
    assert Image.open(io.BytesIO(float_tiff_bytes)).mode == "F"

    prepared = open_result(prepare_image(float_tiff_bytes))

    sampled = [pixel(prepared, (x, 0))[0] for x in (0, 32, 64, 128, 192, 255)]
    assert_close(tuple(sampled), (0, 32, 64, 128, 192, 255))


def test_flat_float_image_does_not_divide_by_zero() -> None:
    """Survive a float image with no range at all, which has no meaningful normalization."""
    buffer = io.BytesIO()
    Image.new("F", (16, 16), 0.5).save(buffer, format="TIFF")

    assert decode_data_uri(prepare_image(buffer.getvalue()))[0] == "image/jpeg"


@pytest.mark.parametrize("mode", ["La", "RGBa"])
def test_premultiplied_alpha_modes_are_flattened(mode: str) -> None:
    """Un-premultiply before flattening, so ``La`` behaves like every other alpha mode.

    ``La`` has no conversion to ``L`` at all, so the fall-through ``convert("RGB")`` raised
    ``ValueError: conversion from La to L not supported`` from inside Pillow with no SDK frame -
    while its sibling ``RGBa`` worked. Both go through the same path now.
    """
    prepared = open_result(prepare_image(Image.new(mode, (40, 30), 0)))

    assert prepared.mode == "RGB"
    assert prepared.size == (40, 30)


@pytest.mark.parametrize(
    ("mode", "clear"),
    [("RGBa", (0, 0, 0, 0)), ("La", (0, 0))],
    ids=["RGBa", "La"],
)
def test_premultiplied_transparency_flattens_onto_white(mode: str, clear: tuple[int, ...]) -> None:
    """The flatten is a real composite onto white, not merely a mode change that happens to work."""
    # Fully transparent premultiplied pixels: every channel is zero, so a dropped alpha shows black.
    prepared = open_result(prepare_image(Image.new(mode, (32, 32), clear)))

    assert_close(pixel(prepared, (16, 16)), (255, 255, 255), tolerance=4)


def test_a_mode_that_cannot_reach_rgb_names_itself() -> None:
    """Any future mode Pillow refuses to convert gets an SDK error naming it, not a raw one."""

    class Stubborn(Image.Image):
        def convert(self, *args: object, **kwargs: object) -> Image.Image:
            raise ValueError("conversion from XYZ to RGB not supported")

    stubborn = Stubborn()
    stubborn._mode = "XYZ"

    with pytest.raises(ValueError, match="cannot be converted to RGB") as caught:
        images_module._flatten(stubborn)

    assert "XYZ" in str(caught.value)
    assert isinstance(caught.value.__cause__, ValueError)


def test_icc_profile_survives_a_re_encode() -> None:
    """Carry a wide-gamut profile across, so colour does not shift at the downscale threshold.

    A compliant image is forwarded verbatim and keeps its profile, so dropping it on the re-encode
    made the same photo render differently at 1024px and at 1025px - a discontinuity nothing in the
    API or the SDK announces.
    """
    oversized, profile = build_icc_jpeg((2000, 1500))
    compliant, _ = build_icc_jpeg((200, 150))

    re_encoded = open_result(prepare_image(oversized))
    passed_through = open_result(prepare_image(compliant))

    assert re_encoded.size == (MAX_IMAGE_DIMENSION, 768)
    assert re_encoded.info.get("icc_profile") == profile
    # The two paths now agree, which is the whole point of carrying it.
    assert passed_through.info.get("icc_profile") == profile


def test_icc_profile_is_dropped_when_the_colour_space_changes(cmyk_jpeg_bytes: bytes) -> None:
    """Never attach a profile that describes pixels the re-encode has already converted away."""
    # A CMYK profile on the RGB JPEG this produces would misrepresent every pixel, which is worse
    # than no profile at all, so only an already-RGB image keeps one.
    assert Image.open(io.BytesIO(cmyk_jpeg_bytes)).mode == "CMYK"

    assert open_result(prepare_image(cmyk_jpeg_bytes)).info.get("icc_profile") is None


def test_an_oversized_icc_profile_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A profile large enough to matter against the byte budget is left behind."""
    oversized, profile = build_icc_jpeg((2000, 1500))
    monkeypatch.setattr(images_module, "MAX_ICC_PROFILE_BYTES", len(profile) - 1)

    assert open_result(prepare_image(oversized)).info.get("icc_profile") is None


# --------------------------------------------------------------------------------------------
# Rejections
# --------------------------------------------------------------------------------------------


def test_animated_gif_is_rejected(animated_gif_bytes: bytes) -> None:
    """Multi-frame images are refused, and the message says how to send one frame."""
    with pytest.raises(ValueError, match="animated"):
        prepare_image(animated_gif_bytes)


def test_animated_gif_is_rejected_as_a_pil_image(animated_gif_bytes: bytes) -> None:
    """The same refusal applies to an already-open animated image."""
    with pytest.raises(ValueError, match="animated"):
        prepare_image(Image.open(io.BytesIO(animated_gif_bytes)))


def test_multi_picture_jpeg_is_accepted_as_the_still_it_is(
    multi_picture_jpeg_bytes: bytes,
) -> None:
    """Send a multi-picture JPEG rather than calling it animated because it has two frames."""
    # An MPF JPEG - a stereo pair, a screennail, some dual-camera captures - is a still photo, and
    # is a plain JPEG on the wire, which the SDK's own preprocess=False path already accepted and
    # the README already promises. Only the safe default path refused it, on a bare n_frames > 1
    # check, with a message telling the caller to pass a still photo when they just had.
    opened = Image.open(io.BytesIO(multi_picture_jpeg_bytes))
    # Older Pillow releases leave this file as a plain single-frame JPEG instead of promoting it to
    # MpoImageFile, so the frame-count hazard simply does not arise there. Assert the premise only
    # where Pillow creates it; the outcome below is required on every version either way.
    if opened.format == "MPO":
        assert getattr(opened, "n_frames", 1) == 2
    else:
        assert opened.format == "JPEG"
        assert getattr(opened, "n_frames", 1) == 1

    prepared = open_result(prepare_image(multi_picture_jpeg_bytes))

    # The primary frame is 3:4 and the embedded second image is 4:3, so the ratio proves which one
    # was sent rather than merely that something was.
    assert prepared.width / prepared.height == pytest.approx(900 / 1200)


def test_animated_message_names_the_format_and_frame_count(animated_gif_bytes: bytes) -> None:
    """Say what was actually seen, so 'pass a still photo' cannot read as a contradiction."""
    with pytest.raises(ValueError, match=r"GIF image has 2 frames"):
        prepare_image(animated_gif_bytes)


def test_animated_gif_is_rejected_with_preprocess_false(animated_gif_bytes: bytes) -> None:
    """Refuse an animation on the pass-through path too, rather than after the upload.

    ``preprocess=False`` sniffed magic bytes only, so an animated GIF was base64'd and sent whole
    for the API to reject - the same image ``preprocess=True`` had already refused locally.
    """
    with pytest.raises(ValueError, match=r"GIF image has 2 frames"):
        prepare_image(animated_gif_bytes, preprocess=False)


def test_animated_webp_is_rejected_with_preprocess_false(animated_webp_bytes: bytes) -> None:
    """The other animatable format the API accepts is counted the same way."""
    with pytest.raises(ValueError, match=r"WEBP image has 2 frames"):
        prepare_image(animated_webp_bytes, preprocess=False)


def test_preprocess_false_still_forwards_a_single_frame_gif() -> None:
    """One frame is not an animation: the check must not cost the still GIF its pass-through."""
    still = build_transparent_gif((40, 30))

    mime, decoded = decode_data_uri(prepare_image(still, preprocess=False))

    assert mime == "image/gif"
    assert decoded == still


def test_preprocess_false_frame_check_does_not_decode_a_jpeg(
    monkeypatch: pytest.MonkeyPatch, small_jpeg_bytes: bytes
) -> None:
    """Only GIF and WEBP are opened to be counted, so the cheap path stays cheap for a photo."""
    opened: list[object] = []
    real_open = Image.open

    def spy(*args: object, **kwargs: object) -> Image.Image:
        opened.append(args[0] if args else None)
        return real_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Image, "open", spy)

    prepare_image(small_jpeg_bytes, preprocess=False)

    assert opened == []


def test_preprocess_false_still_forwards_bytes_pillow_cannot_parse() -> None:
    """A header the sniff accepts but Pillow rejects is left for the API, as it always was."""
    truncated_gif = b"GIF89a" + b"\x00" * 8

    assert decode_data_uri(prepare_image(truncated_gif, preprocess=False)) == (
        "image/gif",
        truncated_gif,
    )


@pytest.mark.parametrize("preprocess", [True, False])
def test_heic_is_refused_by_name_with_a_way_forward(
    heic_header_bytes: bytes, preprocess: bool
) -> None:
    """Name HEIC rather than lumping the most likely rejected input in with corrupt data."""
    # HEIC is the default iPhone camera format, and both paths used to dead-end on it: with
    # preprocess=False the advice was "use preprocess=True to convert other formats", and
    # preprocess=True then claimed the data was not a readable image - which for a valid HEIC is
    # simply false, and sends the developer looking for a truncated download that does not exist.
    with pytest.raises(ValueError, match="HEIC") as excinfo:
        prepare_image(heic_header_bytes, preprocess=preprocess)

    assert "pillow-heif" in str(excinfo.value)


def test_unrecognized_bytes_still_get_the_generic_message() -> None:
    """Keep the HEIC branch narrow: anything else is still reported the way it always was."""
    with pytest.raises(ValueError, match="not a readable image"):
        prepare_image(b"\x00\x00\x00\x18ftypqt  " + bytes(64))


def test_truncated_bytes_are_rejected(truncated_jpeg_bytes: bytes) -> None:
    """A partial download parses as a JPEG header and still has to fail, with a usable message."""
    with pytest.raises(ValueError, match="truncated or corrupt"):
        prepare_image(truncated_jpeg_bytes)


def test_non_image_bytes_are_rejected() -> None:
    """HTML served where an image was expected is a ValueError, not a crash."""
    with pytest.raises(ValueError, match="not a readable image"):
        prepare_image(b"<!doctype html><html><body>404</body></html>")


def test_text_mode_file_object_is_rejected(small_jpeg_path: Path) -> None:
    """A handle opened in text mode is a TypeError naming the fix, not a decoding failure."""
    with (
        small_jpeg_path.open("r", encoding="utf-8") as handle,
        pytest.raises(TypeError, match="rb"),
    ):
        prepare_image(handle)  # type: ignore[arg-type]


def test_file_object_returning_str_is_rejected() -> None:
    """Anything whose ``read()`` hands back ``str`` is refused the same way."""
    with pytest.raises(TypeError, match="rb"):
        prepare_image(io.StringIO("this is not an image"))  # type: ignore[arg-type]


def test_file_object_at_eof_names_the_missing_seek() -> None:
    """Point at the unrewound buffer rather than sending the caller after a corrupt file.

    ``img.save(buf, "JPEG")`` followed by ``prepare_image(buf)`` is one of the commonest Pillow
    slips: the buffer is left at its end, so the read returns nothing. That reached the decoder as
    zero bytes and came back as "the data is not a readable image ... not HTML, a PDF, or a partial
    download", which describes a problem the file does not have.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buffer, "JPEG")

    with pytest.raises(ValueError) as caught:
        prepare_image(buffer)

    message = str(caught.value)
    assert "no bytes" in message
    assert "seek(0)" in message
    assert "not a readable image" not in message

    # The same buffer, rewound, is a perfectly good image: the advice in the message works.
    buffer.seek(0)
    assert open_result(prepare_image(buffer)).size == (32, 32)


def test_closed_file_object_says_it_is_closed(small_jpeg_path: Path) -> None:
    """Name the situation instead of surfacing the interpreter's bare "read of closed file"."""
    handle = small_jpeg_path.open("rb")
    handle.close()

    with pytest.raises(ValueError, match="closed"):
        prepare_image(handle)


def test_closed_file_object_without_a_closed_flag_is_still_explained() -> None:
    """A file-like object that hides ``closed`` still gets an explanation, not a bare ValueError."""

    class Sealed:
        def read(self, *args: object) -> bytes:
            raise ValueError("read of closed file")

    with pytest.raises(ValueError) as caught:
        prepare_image(Sealed())  # type: ignore[arg-type]

    message = str(caught.value)
    assert "could not be read" in message
    assert "read of closed file" in message


def test_unsupported_input_type_is_rejected() -> None:
    """An input that is neither bytes, a path, a handle, nor an image names what is accepted."""
    with pytest.raises(TypeError, match=r"PIL\.Image\.Image"):
        prepare_image(object())  # type: ignore[arg-type]


def test_missing_path_raises_file_not_found(tmp_path: Path) -> None:
    """A path that does not exist surfaces as ``FileNotFoundError``, unwrapped."""
    with pytest.raises(FileNotFoundError):
        prepare_image(tmp_path / "no-such-meal.jpg")


@pytest.mark.parametrize(
    "source",
    ["file:///etc/passwd", "FILE://host/share/meal.jpg", "gopher://example.com/x", "s3://b/k.jpg"],
)
def test_unsupported_uri_scheme_is_named_rather_than_read_as_a_path(source: str) -> None:
    """Refuse a scheme the SDK does not handle instead of turning it into a relative path."""
    # A str that is not an http(s) URL or a data: URI is read from disk, so `file:///etc/passwd`
    # used to become the relative path `file:/etc/passwd` and fail with a FileNotFoundError naming
    # a path nobody wrote. Naming the scheme is both the better error and the narrower behaviour.
    with pytest.raises(ValueError, match="not a supported URI scheme"):
        prepare_image(source)


@pytest.mark.parametrize(
    "source",
    [r"C:\Users\me\lunch.jpg", "D:/photos/meal.png", "my_photo:final.jpg", "lunch.jpg"],
)
def test_a_colon_in_a_path_does_not_make_it_a_uri(source: str) -> None:
    """Keep Windows drive letters and colon-bearing filenames on the filesystem branch."""
    # The guard above must not swallow these: the suite runs on windows-latest, and a colon is a
    # legal character in a POSIX filename. Reaching FileNotFoundError proves the path branch ran.
    with pytest.raises(FileNotFoundError):
        prepare_image(source)


def test_unreadable_path_becomes_a_value_error(tmp_path: Path) -> None:
    """Keep the module's exception set closed when a path exists but cannot be read."""
    # Everything but FileNotFoundError used to escape: a directory path, an empty string (which
    # resolves to "."), an unreadable file. `prepare_image(os.environ.get("MEAL_PHOTO", ""))` is
    # the realistic slip, and it raised IsADirectoryError - outside the ValueError/TypeError/
    # FileNotFoundError set the docstring promises a caller can catch.
    with pytest.raises(ValueError, match="could not read"):
        prepare_image(tmp_path)

    with pytest.raises(ValueError, match="could not read"):
        prepare_image("")


def test_decompression_bomb_is_rejected(
    monkeypatch: pytest.MonkeyPatch, oversized_jpeg_bytes: bytes
) -> None:
    """Pillow's bomb guard is left switched on, and its failure becomes a ValueError."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)

    with pytest.raises(ValueError, match="decompression-bomb"):
        prepare_image(oversized_jpeg_bytes)


def test_decompression_bomb_warning_is_rejected_the_same_way(
    monkeypatch: pytest.MonkeyPatch, oversized_jpeg_bytes: bytes
) -> None:
    """Cover the band between the limit and twice it, where Pillow warns instead of raising."""
    # Pillow raises above 2x MAX_IMAGE_PIXELS and *warns* between 1x and 2x, and the warning class
    # descends from RuntimeWarning rather than OSError, so neither existing handler caught it. In a
    # process running under `-W error` that warning became the exception the caller saw, escaping
    # the module's documented exception set - and which of the two happened depended on how the
    # application had configured `warnings`, not on the image.
    width, height = Image.open(io.BytesIO(oversized_jpeg_bytes)).size
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", (width * height) // 2 + 1)

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with pytest.raises(ValueError, match="decompression-bomb"):
            prepare_image(oversized_jpeg_bytes)


# --------------------------------------------------------------------------------------------
# The byte budget
# --------------------------------------------------------------------------------------------


def encode_at_each_quality(source: bytes) -> dict[int, int]:
    """Replay the re-encode pipeline and report the encoded size at each ladder step."""
    opened = Image.open(io.BytesIO(source))
    opened.load()
    prepared = ImageOps.exif_transpose(opened)
    assert prepared is not None
    prepared.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)

    sizes: dict[int, int] = {}
    for quality in JPEG_QUALITY_LADDER:
        buffer = io.BytesIO()
        prepared.save(buffer, format="JPEG", quality=quality, optimize=True)
        sizes[quality] = len(buffer.getvalue())
    return sizes


def test_quality_ladder_steps_down_until_the_image_fits(
    monkeypatch: pytest.MonkeyPatch, noisy_jpeg_bytes: bytes
) -> None:
    """When the top quality overshoots the budget, the next one down is used - and only that one."""
    sizes = encode_at_each_quality(noisy_jpeg_bytes)
    first, second = JPEG_QUALITY_LADDER[0], JPEG_QUALITY_LADDER[1]
    assert sizes[second] < sizes[first], "fixture does not separate the ladder steps"
    budget = sizes[first] - 1
    monkeypatch.setattr(images_module, "MAX_IMAGE_BYTES", budget)

    _, decoded = decode_data_uri(prepare_image(noisy_jpeg_bytes))

    assert len(decoded) <= budget
    assert len(decoded) == sizes[second]


def test_image_that_never_fits_is_rejected(
    monkeypatch: pytest.MonkeyPatch, noisy_jpeg_bytes: bytes
) -> None:
    """Once the ladder is exhausted the caller is told, rather than sent an over-budget body."""
    monkeypatch.setattr(images_module, "MAX_IMAGE_BYTES", 512)

    with pytest.raises(ValueError, match=f"quality {JPEG_QUALITY_LADDER[-1]}"):
        prepare_image(noisy_jpeg_bytes)


# --------------------------------------------------------------------------------------------
# preprocess=False
# --------------------------------------------------------------------------------------------


def test_preprocess_false_forwards_bytes_untouched(oversized_jpeg_bytes: bytes) -> None:
    """Nothing is decoded, rotated, or resized: an oversized image goes out at full size."""
    prepared = prepare_image(oversized_jpeg_bytes, preprocess=False)
    mime, decoded = decode_data_uri(prepared)

    assert mime == "image/jpeg"
    assert decoded == oversized_jpeg_bytes
    assert open_result(prepared).size == (2000, 1500)


@pytest.mark.parametrize(
    ("expected_mime", "sample"),
    [
        ("image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32),
        ("image/gif", b"GIF89a" + b"\x00" * 32),
        ("image/webp", b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32),
    ],
)
def test_preprocess_false_sniffs_the_format(expected_mime: str, sample: bytes) -> None:
    """The media type comes from the magic bytes, with no decode to confirm it."""
    assert decode_data_uri(prepare_image(sample, preprocess=False))[0] == expected_mime


def test_preprocess_false_rejects_an_unknown_format() -> None:
    """An unrecognized header is refused, and the message lists what is accepted."""
    with pytest.raises(ValueError, match="WEBP"):
        prepare_image(b"BM\x00\x00\x00\x00 not really a bitmap", preprocess=False)


def test_preprocess_false_rejects_oversized_bytes(
    monkeypatch: pytest.MonkeyPatch, small_jpeg_bytes: bytes
) -> None:
    """The size check still applies, and the message points at ``preprocess=True``."""
    monkeypatch.setattr(images_module, "MAX_IMAGE_BYTES", 64)

    with pytest.raises(ValueError, match="64 byte limit") as caught:
        prepare_image(small_jpeg_bytes, preprocess=False)

    assert "preprocess=True" in str(caught.value)


def test_preprocess_false_rejects_a_pil_image(small_jpeg_bytes: bytes) -> None:
    """An in-memory image has no encoded bytes to forward, so the combination is refused."""
    with pytest.raises(ValueError, match="preprocess=False"):
        prepare_image(Image.open(io.BytesIO(small_jpeg_bytes)), preprocess=False)


# --------------------------------------------------------------------------------------------
# Input shapes
# --------------------------------------------------------------------------------------------


def test_path_input_is_read(small_jpeg_path: Path, small_jpeg_bytes: bytes) -> None:
    """A ``pathlib.Path`` is read from disk."""
    assert decode_data_uri(prepare_image(small_jpeg_path))[1] == small_jpeg_bytes


def test_str_path_input_is_read(small_jpeg_path: Path, small_jpeg_bytes: bytes) -> None:
    """A path given as a plain string is read from disk too, once it is not a URI."""
    assert decode_data_uri(prepare_image(str(small_jpeg_path)))[1] == small_jpeg_bytes


def test_binary_file_object_is_read(small_jpeg_path: Path, small_jpeg_bytes: bytes) -> None:
    """An open binary handle is read in full."""
    with small_jpeg_path.open("rb") as handle:
        assert decode_data_uri(prepare_image(handle))[1] == small_jpeg_bytes


@pytest.mark.parametrize("wrap", [bytearray, memoryview])
def test_buffer_inputs_are_accepted(
    wrap: type[bytearray] | type[memoryview], small_jpeg_bytes: bytes
) -> None:
    """``bytearray`` and ``memoryview`` are handled like ``bytes``."""
    assert decode_data_uri(prepare_image(wrap(small_jpeg_bytes)))[1] == small_jpeg_bytes


def test_pil_image_input_is_encoded() -> None:
    """An image built in memory is always re-encoded, since it has no original bytes."""
    image = Image.new("RGB", (64, 48), (12, 200, 90))

    prepared = prepare_image(image)
    result = open_result(prepared)

    assert decode_data_uri(prepared)[0] == "image/jpeg"
    assert result.size == (64, 48)
    assert_close(pixel(result, (32, 24)), (12, 200, 90))


def test_pil_image_input_is_not_mutated() -> None:
    """Preparing an image the caller still holds leaves their copy at its original size."""
    image = Image.open(io.BytesIO(build_gradient_jpeg((1600, 1200))))
    image.load()

    prepare_image(image)

    assert image.size == (1600, 1200)


def test_importing_the_sdk_does_not_import_pillow() -> None:
    """Keep Pillow out of a process that only ever calls the seventeen non-image operations."""
    # Pillow is a hard dependency and stays one - the README's very first example scans a local
    # file, and an extra would break that on a default install. But only one of the eighteen
    # operations can carry an image at all, and even it skips Pillow for a URL, a data: URI, or
    # preprocess=False, so importing sixteen modules and several megabytes of codec libraries at
    # package import was a cost almost every process paid for nothing. This test is the thing that
    # keeps a stray module-scope `from PIL import ...` from quietly putting it back.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, january_ai; print(any(name.startswith('PIL') for name in sys.modules))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "False"


def test_the_image_path_still_works_with_pillow_imported_lazily(small_jpeg_bytes: bytes) -> None:
    """Confirm the deferred import resolves when an image actually arrives."""
    assert decode_data_uri(prepare_image(small_jpeg_bytes))[0] == "image/jpeg"
