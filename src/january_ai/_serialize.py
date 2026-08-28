"""Input serialization shared by the resource methods.

These helpers turn the Python values callers naturally have - ``datetime`` and ``date`` objects,
models handed back by a previous call - into the exact JSON shapes the API expects, and they fail
loudly and early when a value cannot be sent correctly. Nothing here imports a response model, so
the direction of dependency stays one-way: resources depend on models and on this module, and this
module depends on neither.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Protocol, TypeAlias

__all__ = [
    "DetectionInput",
    "SupportsModelDump",
    "serialize_detections",
    "to_iso_date",
    "to_iso_datetime",
    "to_json_value",
]


class SupportsModelDump(Protocol):
    """Structural type for a pydantic model: anything that can dump itself to JSON values.

    Typing the input this way lets a caller pass models this SDK returned, models from a newer
    release, or their own equivalents, without this module importing any of them.
    """

    def model_dump(self, *, mode: str = ..., exclude_none: bool = ...) -> dict[str, object]:
        """Return the model's fields as JSON-compatible values."""
        ...


DetectionInput: TypeAlias = SupportsModelDump | Mapping[str, object]
"""One detection to send back for correction: the model from a scan, or a plain dict."""


def to_iso_datetime(value: datetime | str, *, field: str) -> str:
    """Render a timestamp as an ISO 8601 string with an explicit UTC offset.

    A naive ``datetime`` is rejected rather than guessed at. The API resolves timestamps against a
    real instant, so a value without an offset would silently be interpreted in a timezone the
    caller did not choose and place a meal at the wrong hour of the day.

    Anything that is neither a ``datetime`` nor a string is refused here too, by name. A ``date``
    is the likely mistake - the SDK takes one wherever a calendar day is meant, and the two
    parameter families sit next to each other - and left alone it reaches ``value.tzinfo`` and
    raises an ``AttributeError`` that names neither this SDK nor the argument.

    Args:
        value: An aware ``datetime``, or a string already in the format the API expects, which is
            passed through untouched.
        field: The parameter name, used in the error message so the caller knows which argument to
            fix.

    Returns:
        The ISO 8601 representation of ``value``.

    Raises:
        ValueError: If ``value`` is a ``datetime`` with no timezone information.
        TypeError: If ``value`` is neither a ``datetime`` nor a ``str``.
    """
    if isinstance(value, str):
        return value
    # datetime is a subclass of date, so this rejects a plain date while accepting a datetime.
    if not isinstance(value, datetime):
        raise TypeError(
            f"{field} must be a timezone-aware datetime or an ISO 8601 string; got "
            f"{type(value).__name__}." + _timestamp_hint(value)
        )
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{field} requires a timezone-aware datetime; got a naive one. "
            "Use datetime.now(timezone.utc) or attach a tzinfo."
        )
    return value.isoformat()


def _timestamp_hint(value: object) -> str:
    """Suggest the fix for the two values most often passed where a timestamp belongs."""
    if isinstance(value, date):
        return (
            " A date names a day rather than an instant; combine it with a time and a timezone, "
            "as in datetime.combine(value, time(12, 0), tzinfo=timezone.utc)."
        )
    if isinstance(value, (int, float)):
        return (
            " An epoch number is not accepted; convert it with "
            "datetime.fromtimestamp(value, tz=timezone.utc)."
        )
    return ""


def to_iso_date(value: date | datetime | str, *, field: str) -> str:
    """Render a calendar date as ``YYYY-MM-DD``.

    A ``datetime`` contributes only its date part. No timezone check applies here: a calendar date
    names a day rather than an instant, and the API resolves day ranges against the end user's own
    timezone.

    Args:
        value: A ``date``, a ``datetime``, or a string already in the format the API expects, which
            is passed through untouched.
        field: The parameter name, accepted for symmetry with :func:`to_iso_datetime` and reserved
            for error messages.

    Returns:
        The ``YYYY-MM-DD`` representation of ``value``.
    """
    if isinstance(value, str):
        return value
    # datetime is a subclass of date, so it has to be tested first or its time part would be
    # dropped by date.isoformat() only by accident of the base class implementation.
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def to_json_value(value: object) -> object:
    """Render a request body as JSON-safe values, dumping any pydantic model it contains.

    The request parameters are documented as ``TypedDict`` shapes, and mypy flags a model passed
    where one belongs. Nothing stops an untyped caller from passing the model anyway, though, and
    the models the SDK hands *back* are an inviting source of one: ``LoggedFood.consumed_serving``
    is the very ``ServingSelection`` that a food-log update wants inside its ``serving`` key. Left
    alone it reaches ``json.dumps`` and raises a bare ``TypeError`` from the standard library that
    names neither the SDK nor the parameter, so models are dumped here instead - at any depth,
    since the inviting case is a model nested inside an otherwise plain dict.

    Args:
        value: A request body, or any value found inside one.

    Returns:
        The value with every model replaced by its JSON-mode dump. Values that are already
        JSON-safe are returned unchanged.
    """
    if isinstance(value, Mapping):
        return {key: to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped: dict[str, object] = dump(mode="json", exclude_none=True)
        return dumped
    return value


def serialize_detections(detections: Sequence[DetectionInput]) -> list[dict[str, object]]:
    """Convert scan detections into the JSON array the corrections endpoint expects.

    Detections are meant to be sent back exactly as the scan returned them. Models are dumped in
    JSON mode with ``None`` values dropped, which round-trips any field the server added after this
    release - models keep unknown fields - and lets the API fill in omitted zero-value nutrient
    keys itself. Mappings are copied as-is for callers holding raw decoded JSON.

    Args:
        detections: The ``detections`` array from a photo or text scan, as models or as mappings.

    Returns:
        A list of plain dictionaries safe to place in a JSON request body.
    """
    serialized: list[dict[str, object]] = []
    for detection in detections:
        if isinstance(detection, Mapping):
            serialized.append(dict(detection))
        else:
            serialized.append(detection.model_dump(mode="json", exclude_none=True))
    return serialized
