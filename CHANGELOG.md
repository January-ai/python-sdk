# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Names prefixed with an
underscore are internal and are not covered by the versioning guarantee.

## [Unreleased]

### Added

- `APIResponseValidationError`, raised when a success response is valid JSON but not the shape the
  release expects. It derives from `JanuaryError` and keeps the underlying
  `pydantic.ValidationError` as `__cause__`, so `except JanuaryError` now genuinely covers every
  failure that can occur once a request is under way.
- `APIStatusError.retry_after`, the `Retry-After` header in seconds on **every** status class rather
  than on `RateLimitError` alone. A `503` telling the caller to back off for ten minutes now says so
  through the error instead of only through the raw headers. `RateLimitError.retry_after` is
  unchanged; it is now inherited.
- **A total `Retry-After` budget of 60 seconds per call.** The per-wait cap alone let a server
  repeating `Retry-After: 60` hold a single method call for `max_retries × 60` seconds - five
  minutes at `max_retries=5`, which `timeout=` does not bound because it covers one attempt. Waits
  the *server* directs are now summed across the attempts of one call and the last error is raised
  once honouring the next one would exceed the budget. The SDK's own backoff is not charged to it.
  Whichever bound is hit, the raised error explains itself and names the wait the server asked for.
- **An ICC profile survives the re-encode** when the image is already RGB and the profile is a
  reasonable size, so a wide-gamut photo no longer shifts colour at exactly the size where
  downscaling begins - a pass-through image kept its profile, a re-encoded one did not.

### Fixed

- **The decompression-bomb guard is actually enforced.** Pillow raises `DecompressionBombError`
  only above *twice* `Image.MAX_IMAGE_PIXELS`; between one and two times it merely emits a
  `DecompressionBombWarning`, which Python's default filters print and discard. The SDK caught the
  warning class but never turned it into one, so an image at 1.9x the limit decoded in full unless
  the host application had itself turned warnings into errors - roughly 537 MB of resident memory
  for an attacker's few-hundred-kilobyte file at Pillow's own ~89.5-megapixel default. Both decode
  sites, `preprocess=True` and the frame-count probe on the `preprocess=False` path, now raise the
  warning inside a `warnings.catch_warnings()` window kept as small as the decode calls themselves,
  and it surfaces as the same `ValueError` as the error above the band. Pillow's limit is not
  raised and the guard is not disabled.
- **`auth.create_client_token` is no longer replayed after an ambiguous failure.** Minting is not
  idempotent and the API accepts no idempotency key, so a read timeout, a dropped connection, or a
  retryable 5xx from a gateway that had already forwarded the request sent the POST again and minted
  a second token the caller never sees - the raw value is returned exactly once - valid until its
  TTL expires and clearable only by revoking every token the end user holds. A `ReadTimeout` used to
  produce three POSTs; it now produces one. `revoke_client_tokens` is unchanged and still retries,
  because revocation is idempotent.
- **`default_headers` can no longer override `Content-Type`.** `Content-Type` was left to httpx,
  which sets it only where nothing has claimed the name, so
  `January(default_headers={"Content-Type": "text/plain"})` shipped a JSON body advertised as text.
  Every body the SDK sends is JSON, so the header is now pinned alongside `Authorization` and
  `Accept` on any request that carries one - and still left off requests that do not, since a GET
  has no content to type.
- **`base_url` validation covers the shapes that parse but cannot carry a path.** A scheme with no
  host (`https://`, `https:///v1`), a query string, and a fragment were all accepted and then built
  request URLs like `https:/v1.2/credits` and
  `https://api.example.com?token=abc/v1.2/credits`, where the operation path lands inside the query
  and vanishes. Each is now refused at construction with an error naming its own problem. A path
  prefix (`https://proxy.example.com/january`, a gateway in front of the API) and a trailing slash
  are still accepted.
- **Client tokens may omit `end_user_id` on food-log calls.** The SDK required the identifier on all
  four operations whatever the credential, and raised `ValueError` locally before sending. The API's
  own description of `x-end-user-id` says it is "Required with an API key, which carries no user of
  its own. A client token already names its end user, so it may omit this header", and a live `ct-`
  token with `food_logs:read` and no header answers `200`. The local check now follows the
  credential, which unblocks exactly the device-side case client tokens exist for; an `sk-` key, and
  any credential whose prefix the SDK does not recognize, keeps the early error.
- **A `date` where a timestamp belongs raises a `TypeError` that names the argument.** Passing one
  as `timestamp_utc` or `start_time` went straight to `value.tzinfo` and raised
  `AttributeError: 'datetime.date' object has no attribute 'tzinfo'`, naming neither the SDK nor the
  parameter; a `date` inside `cgm_data` or `consumed_foods` reached `json.dumps` and raised
  `Object of type date is not JSON serializable`, naming neither the parameter nor the entry. Both
  now say which argument to fix and how.
- **A non-ASCII `end_user_id` or `end_user_timezone` raises a `ValueError` that names it.** An email
  address with an accent or a non-Latin username surfaced as httpx's
  `'ascii' codec can't encode character '\xe9' in position 3`, which names neither the SDK, the
  parameter, nor the constraint. Control characters are refused with the same message, so the SDK's
  wording is what a caller sees rather than httpx's, which quotes the whole offending header line.
- **A file object at EOF says so.** `img.save(buf, "JPEG")` followed by `prepare_image(buf)` without
  a `seek(0)` read zero bytes and came back as "the data is not a readable image ... not HTML, a
  PDF, or a partial download" - a description of a problem the file does not have. An already-closed
  file object is named as closed rather than surfacing a bare `read of closed file`.
- **Premultiplied-alpha images no longer leak a raw Pillow error.** Mode `La` has no conversion to
  `L` at all, so it raised `ValueError: conversion from La to L not supported` from inside Pillow
  with no SDK frame, while its sibling `RGBa` worked. Both are un-premultiplied before flattening,
  and any mode Pillow refuses to convert now yields an SDK error naming the mode.
- **An animated GIF or WEBP is refused with `preprocess=False` too.** That path sniffed magic bytes
  only, so an animation was base64'd and uploaded whole for the API to reject - the same image
  `preprocess=True` had already refused locally. Frames are counted from the container headers, and
  only for the two formats that can hold more than one.
- **A huge `message` in an error body is truncated.** The 200-character bound applied only to
  non-JSON bodies, so a two-million-character `message` produced a two-million-character exception
  string. The untruncated value remains in `.body`.

- **`food_logs.create` no longer replays a retryable 5xx.** `retry_ambiguous=False` was consulted
  only on the transport-error path, so a `502`, `503`, or `504 upstream_timeout` - a gateway
  reporting that it forwarded the request to an origin whose fate it does not know - was retried and
  could log the meal twice. A `429` is still retried, because rate limiting is refused before the
  handler runs.
- **The API key no longer reaches a traceback frame.** The assembled request headers were bound to a
  local on the frame that raises, so anything capturing frame locals (`pytest --showlocals`, an
  error reporter) could read the live credential out of an ordinary timeout or `429`.
- **A 3xx is reported instead of being treated as a success.** Redirects were decoded as successes,
  and their empty body became a `None` returned through an annotation that promises a model. The
  error now names the `Location` and the configured `base_url`.
- **An empty success body is reported rather than returned as `None`.** Seventeen of the eighteen
  operations declare a body; only the operation's own declaration can produce a `None` now, so a
  proxy answering `200` with nothing no longer surfaces as an `AttributeError` somewhere unrelated.
- **`httpx.ProxyError` is classified as a pre-send failure.** It descends from `TransportError`
  rather than `NetworkError`, so it fell through to "fatal" and silently disabled retrying for
  anyone behind a proxy - which merely having `HTTPS_PROXY` set arranges.
- **High-bit-depth images are rescaled, not clipped.** A 16-bit greyscale PNG or TIFF (`I;16`, `I`)
  and a float TIFF (`F`) arrived as near-solid white, silently, because `convert("RGB")` clips.
  Every wide-integer mode now also forces a re-encode, so none of them can be judged
  already-compliant and forwarded verbatim before the rescale runs. Which mode name Pillow reports
  is a version detail - a 16-bit greyscale PNG is `I` before Pillow 11 and `I;16` from Pillow 11 on
  - so the SDK now treats the same file the same way across its whole supported Pillow range.
- **Multi-picture JPEGs are accepted.** An MPF/MPO still - a stereo pair, an embedded screennail,
  some dual-camera captures - was refused as animated on a bare frame count, though the same bytes
  were accepted with `preprocess=False`.
- **HEIC/HEIF/AVIF is refused by name**, pointing at `pillow-heif`, instead of sharing the generic
  "not a readable image" message with corrupt data and HTML.
- **A closed client raises `JanuaryError`** rather than letting httpx raise a bare `RuntimeError`.
  A client whose `httpx` client you supplied keeps working, since `close()` does not close it.
- **Unreadable image paths raise `ValueError`.** A directory, an empty string, or a file without
  read permission escaped as `IsADirectoryError`/`PermissionError`, outside the documented set. A
  Pillow decompression-bomb *warning* is now caught alongside the error - and, per the entry above,
  raised rather than merely caught.
- **A URI scheme the SDK does not support is named** rather than read as a relative filesystem path,
  so `file:///etc/passwd` no longer fails as a missing file called `file:/etc/passwd`.
- **`base_url` is validated at construction.** A forgotten scheme now raises `ValueError` where the
  mistake is.
- **Pydantic models passed where a `Param` dict belongs are dumped** instead of reaching
  `json.dumps` and raising a bare `TypeError` from the standard library. This matters most for
  read-modify-write, where `LoggedFood.consumed_serving` is exactly the `ServingSelection` a
  food-log update wants.

### Changed

- **The release workflow verifies before it publishes.** A `v*` tag went straight to checkout, a
  tag/version assertion, `uv build`, a wheel-contents check, `twine check`, and an irreversible PyPI
  upload - without running ruff, mypy, or a single test, on a commit CI may never have seen. A
  `verify` job now runs the whole gate - ruff, ruff format, mypy over `src tests examples`, and
  pytest - on Python 3.10 and 3.14, and `build` depends on it, so a red suite blocks the release.
  The tag/version assertion is unchanged.
- **The credit documentation no longer overstates billing.** The SDK stated that every successful
  `/v1.2` call except `credits.get()` costs a credit. Measured against the live API by reading
  `used_credits` either side of each call, `auth.create_client_token` and `auth.revoke_client_tokens`
  bill nothing either. The README, the `Credits` docstrings, the `used_credits` field description,
  and `examples/04_client_tokens.py` now say so, and say that it was measured rather than promised by
  the published spec - so it should not be hard-coded as a contract.
- Pillow is imported lazily, so `import january_ai` no longer loads it for the seventeen operations
  that cannot decode an image. It remains a required dependency.
- Documentation corrections throughout: the `JanuaryError` hierarchy no longer claims to cover local
  argument mistakes; the `Retry-After` caps are described as a per-wait and a per-call bound; metadata
  stripping and alpha flattening are scoped to the re-encode path; the pass-through predicate is
  stated correctly; the logging recipe no longer enables `httpx`, which logs the query string the
  SDK deliberately strips; a client-level `timeout=` is no longer called "global"; README links are
  absolute so they resolve on PyPI.

## [0.1.0] - 2026-08-28

Initial release: a fully typed Python client for the January AI nutrition intelligence API,
targeting API version `/v1.2`.

### Added

- **Two clients, one surface.** `January` (blocking) and `AsyncJanuary` (asyncio and trio, sleeping
  through `anyio`) are identical in configuration, resources, method names, arguments, and return
  types; the only difference is `await`. Both are context managers and both leave an `httpx` client
  you supplied for you to close.
- **All 18 operations across seven resource groups.**
  - `auth` - mint a scoped, short-lived client token for one end user, and revoke every token that
    user holds.
  - `credits` - the plan's allowance, consumption, and reset date for the current billing period.
  - `foods` - full-text search, type-ahead autocomplete, fetch one food with its complete list of
    servings, barcode lookup, and healthier alternatives filtered by dietary restrictions and
    preferences.
  - `restaurants` - restaurants near a point, and menu-item search across them.
  - `food_scans` - photo and text food recognition, plus conversational correction of a result.
  - `food_logs` - a per-end-user food diary: create, list by date range, partially update, delete.
  - `glucose` - the predicted glucose response to a meal, optionally personalized with CGM history.
- **Image preparation** (`prepare_image`, and `preprocess=True` on `food_scans.scan_photo`) that
  accepts a path, an http(s) URL, a `data:` URI, raw bytes, an open binary file, or a
  `PIL.Image.Image`. It applies EXIF orientation, caps the longest side at 1024 px, flattens alpha
  onto white, re-encodes as JPEG stepping down through quality 85/75/65 to fit the byte budget, and
  strips all metadata including GPS. URLs and data URIs pass through byte for byte; an already
  compliant image is sent without re-encoding.
- **Retries with jittered exponential backoff**, defaulting to 2 attempts beyond the first,
  0.5s doubling to a cap of 8s. Retry decisions are made on the API's error `code` first and the
  HTTP status only when the code is unknown. `Retry-After` is honoured up to 60 seconds; longer
  waits are raised to the caller instead of slept through.
- **Transport failures are classified before replay.** Pre-send failures are always retried,
  ambiguous post-send failures only where a duplicate is harmless, and everything else never.
  `food_logs.create` opts out of ambiguous replay, since the API has no idempotency key for it.
- **A per-operation timeout policy.** 60 seconds overall with a 5-second connect timeout by default;
  120 seconds for the three food-scan operations, which run model inference server-side. Overridable
  per client and per call.
- **An exception hierarchy rooted at `JanuaryError`**, with `APIConnectionError`, `APITimeoutError`,
  and an `APIStatusError` tree covering 400, 401, 403, 404, 413, 429, and 5xx.
  `CreditLimitExceededError` is deliberately not a subclass of `RateLimitError`, so a sleep-and-retry
  handler cannot swallow an exhausted monthly allowance. Errors carry `status_code`, `code`,
  `message`, `docs_url`, `request_id`, `response`, and the parsed `body`.
- **Response models in `january_ai.types`**, transcribed from the OpenAPI schemas and named after
  them with the `Dto` suffix removed. Every model allows unknown fields, and enum-like response
  fields are typed `str`, so a server-side addition never breaks a build. Request vocabularies are
  `Literal` types and request shapes are `TypedDict`s.
- **Three-way end-user resolution.** Omitting `end_user_id` uses the client's `default_end_user_id`,
  passing a string overrides it, and passing `None` suppresses the header entirely. Food-log
  operations require an end user and raise `ValueError` locally, before any HTTP call, when they do
  not have one.
- **Credential hygiene.** The `Authorization` header, API keys, request bodies, and query strings are
  never logged, and `repr()` on a client redacts the key to `'sk-***'` or `'ct-***'`.
- **`py.typed`**, so mypy and pyright type-check against the package with no stubs.
- Five runnable examples in `examples/`, covering photo scans, search-and-log, glucose prediction,
  client tokens, and concurrent async scans.

[Unreleased]: https://github.com/January-ai/python-sdk/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/January-ai/python-sdk/releases/tag/v0.1.0
