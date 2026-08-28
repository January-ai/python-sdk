"""Sentinels, type aliases, and the request description shared across the SDK.

Two ideas live here. :class:`NotGiven` distinguishes "the caller said nothing" from "the caller
explicitly said ``None``", which matters wherever ``None`` is itself a meaningful value: omitting
``end_user_id`` falls back to the client default, while passing ``None`` suppresses the header
even when a default exists. :class:`RequestSpec` is the single immutable value the resource layer
builds and the transport layer consumes, so every operation is described in one place and the
client never has to guess how to send it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, ClassVar, Generic, Literal, TypeAlias, TypeVar, Union

import httpx

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

__all__ = [
    "NOT_GIVEN",
    "ImageInput",
    "NotGiven",
    "NotGivenOr",
    "RequestSpec",
    "ResponseT",
]

T = TypeVar("T")

ResponseT = TypeVar("ResponseT")
"""The model a request deserializes into, or the operation's return type."""


class NotGiven:
    """Sentinel for "argument omitted", distinct from an explicit ``None``.

    The single instance is :data:`NOT_GIVEN`. It is falsy, so ``if timeout:`` behaves the way a
    reader expects, and it is a singleton, so ``value is NOT_GIVEN`` holds no matter which module
    the sentinel was imported through.
    """

    _instance: ClassVar[NotGiven | None] = None

    def __new__(cls) -> NotGiven:
        """Return the one and only instance, creating it on first use."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> Literal[False]:
        """Report the sentinel as falsy."""
        return False

    def __repr__(self) -> str:
        """Render as ``NOT_GIVEN`` so repeated arguments read cleanly in tracebacks."""
        return "NOT_GIVEN"


NOT_GIVEN: NotGiven = NotGiven()
"""The omitted-argument sentinel. Compare with ``is``, never with ``==``."""

NotGivenOr: TypeAlias = T | NotGiven
"""A value of type ``T`` that the caller may leave unspecified."""

ImageInput: TypeAlias = Union[
    str, os.PathLike[str], bytes, bytearray, memoryview, IO[bytes], "PILImage"
]
"""Everything :func:`january_ai.prepare_image` accepts for a food photo.

An ``str`` is either an ``http(s)`` URL, a ``data:`` URI, or a filesystem path; URLs and data URIs
are forwarded to the API untouched, anything else is read from disk. Raw ``bytes``, a binary file
object, and a ``PIL.Image.Image`` are encoded into a data URI by the SDK.
"""


# Build this unsubscripted - RequestSpec(method=..., cast_to=Food) - and let the type parameter be
# inferred from cast_to or from the annotated target. Calling the subscripted alias,
# RequestSpec[Food](...), raises TypeError on Python 3.10, the floor: typing assigns __orig_class__
# on the new instance, and the frozen __setattr__ that dataclasses generated before slots=True
# rebuilt the class refuses it. Python 3.11 guards that assignment.
@dataclass(frozen=True, slots=True)
class RequestSpec(Generic[ResponseT]):
    """An immutable description of one HTTP request and how to interpret its response.

    Resource methods build a spec and hand it to the client; all header assembly, query-parameter
    cleaning, retrying, and deserialization happen in the transport layer from these fields alone.

    Attributes:
        method: The HTTP method, one of ``GET``, ``POST``, ``PATCH``, or ``DELETE``.
        path: The path including the ``/v1.2`` prefix, appended to the client's base URL.
        cast_to: The model to validate the response body into, or ``None`` when the operation
            returns no body.
        params: Query parameters. Entries whose value is ``None`` or :data:`NOT_GIVEN` are dropped
            by the client rather than sent as empty strings.
        json_body: The JSON request body, or ``None`` to send no body.
        end_user_id: The value for the ``x-end-user-id`` header. :data:`NOT_GIVEN` falls back to
            the client's ``default_end_user_id``; an explicit ``None`` omits the header even when
            a default exists.
        end_user_timezone: An IANA timezone name for the ``x-end-user-timezone`` header, sent only
            when not ``None``.
        timeout: A per-call timeout override. :data:`NOT_GIVEN` uses ``default_timeout`` and then
            the client's own timeout.
        default_timeout: The operation's own default timeout, used when the caller passes none.
            The food-scan endpoints run model inference and set this to ``SCAN_TIMEOUT``.
        retry_ambiguous: Whether a transport failure that may have reached the server can be
            replayed. ``False`` for non-idempotent operations such as creating a food log, where a
            replay risks a duplicate.
    """

    method: str
    path: str
    cast_to: type[ResponseT] | None = None
    params: Mapping[str, object] | None = None
    json_body: Mapping[str, object] | None = None
    end_user_id: str | NotGiven | None = NOT_GIVEN
    end_user_timezone: str | None = None
    timeout: float | httpx.Timeout | NotGiven = NOT_GIVEN
    default_timeout: httpx.Timeout | None = None
    retry_ambiguous: bool = True
