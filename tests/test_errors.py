"""Error mapping: exactly which exception the SDK raises for which error response.

The API answers every failure with ``{message, code, docs_url}`` and documents ``code`` as the
stable machine-readable identifier that clients should branch on. This module pins the whole table
down - every documented ``(status, code)`` pair against the exact class it must produce - and then
covers the ways reality departs from the documentation: a status the SDK has no class for, an HTML
page from a gateway that answered instead of the API, and an empty body from a proxy that gave up.

Retries are switched off throughout (``max_retries=0``), so each assertion is about mapping alone
and never about the retry loop; retry behaviour is covered in ``test_retries.py``.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx
from pydantic import ValidationError

from january_ai import (
    APIResponseValidationError,
    APIStatusError,
    AsyncJanuary,
    AuthenticationError,
    BadRequestError,
    CreditLimitExceededError,
    InternalServerError,
    January,
    JanuaryError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
)

from .conftest import AsyncClientFactory, ClientFactory

BASE_URL = "https://partners.january.ai"
CREDITS_URL = f"{BASE_URL}/v1.2/credits"

# A complete, valid credits body, for the tests that need the happy path to still work.
CREDITS_PAYLOAD: dict[str, object] = {
    "plan": "free",
    "period_start": "2026-08-01",
    "period_end": "2026-08-31",
    "resets_at": "2026-09-01T00:00:00.000Z",
    "included_credits": 1000,
    "used_credits": 342,
    "remaining_credits": 658,
}

DOCS_URL = "https://docs.january.ai/rest-api/api-overview"

# A gateway that answers instead of the API returns HTML, not the documented error envelope.
HTML_GATEWAY_BODY = (
    "<html><head><title>502 Bad Gateway</title></head>"
    "<body><center><h1>502 Bad Gateway</h1></center><hr><center>nginx</center></body></html>"
)

# Every (status, code) pair the API documents, with the class the SDK must raise for it. Note the
# two 429 rows: one status, two entirely different conditions, told apart only by the code. The 501
# row is the one place where the family is broader than the name: not_implemented is permanent
# rather than transient, but it is still a 5xx and the SDK gives every 5xx the same class, so what
# the caller branches on there is the code rather than the class.
STATUS_CODE_CASES: list[tuple[int, str, type[APIStatusError]]] = [
    (400, "invalid_request", BadRequestError),
    (401, "unauthorized", AuthenticationError),
    (403, "forbidden", PermissionDeniedError),
    (404, "not_found", NotFoundError),
    (413, "payload_too_large", PayloadTooLargeError),
    (429, "rate_limited", RateLimitError),
    (429, "credit_limit_exceeded", CreditLimitExceededError),
    (500, "internal_error", InternalServerError),
    (501, "not_implemented", InternalServerError),
    (502, "upstream_error", InternalServerError),
    (503, "service_unavailable", InternalServerError),
    (504, "upstream_timeout", InternalServerError),
]


def error_body(code: str, *, message: str | None = None) -> dict[str, str]:
    """Build the error envelope the API documents: a message, a code, and a docs link."""
    return {
        "message": message if message is not None else f"The request failed: {code}.",
        "code": code,
        "docs_url": DOCS_URL,
    }


@pytest.fixture
def no_retry_client(make_client: ClientFactory) -> January:
    """A client that never retries, so a test sees exactly the one response it mocked."""
    return make_client(max_retries=0)


@pytest.fixture
async def no_retry_async_client(make_async_client: AsyncClientFactory) -> AsyncJanuary:
    """The asynchronous counterpart of :func:`no_retry_client`."""
    return make_async_client(max_retries=0)


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    STATUS_CODE_CASES,
    ids=[f"{status}-{code}" for status, code, _ in STATUS_CODE_CASES],
)
def test_documented_failures_raise_their_exact_exception_class(
    no_retry_client: January,
    respx_mock: respx.MockRouter,
    status: int,
    code: str,
    expected: type[APIStatusError],
) -> None:
    """Map every documented (status, code) pair onto the precise class it must raise."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(status, json=error_body(code)),
    )

    with pytest.raises(APIStatusError) as excinfo:
        no_retry_client.credits.get()

    # Exact class, not isinstance: a 429 that arrives as CreditLimitExceededError must not also
    # satisfy an `except RateLimitError` written by the caller, and vice versa.
    assert type(excinfo.value) is expected
    assert excinfo.value.status_code == status
    assert excinfo.value.code == code


def test_credit_limit_is_a_sibling_of_rate_limit_not_a_subclass() -> None:
    """Keep an exhausted credit allowance out of reach of a rate-limit retry handler."""
    # This is the one relationship in the tree that a well-meaning refactor could quietly break.
    # The idiomatic way to handle a 429 is `except RateLimitError: sleep(retry_after); retry()`.
    # A rate limit clears when its window rolls over, so that loop terminates. A credit allowance
    # returns only at the start of the next calendar month and carries no Retry-After, so the same
    # loop would spin forever against an account that has simply run out of credits, hiding a
    # billing problem behind what looks like a slow API. Making CreditLimitExceededError a sibling
    # rather than a subclass forces the caller to notice it.
    assert not issubclass(CreditLimitExceededError, RateLimitError)
    assert not issubclass(RateLimitError, CreditLimitExceededError)

    # Both are still status errors, so `except APIStatusError` and `except JanuaryError` continue
    # to catch either one.
    assert issubclass(CreditLimitExceededError, APIStatusError)
    assert issubclass(RateLimitError, APIStatusError)


def test_status_error_carries_every_documented_field(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Populate message, status_code, code, docs_url, request_id, response, and body."""
    body = error_body("not_found", message="No food has id 999999999.")
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(404, json=body, headers={"x-request-id": "req_01J8Z4K3"}),
    )

    with pytest.raises(NotFoundError) as excinfo:
        no_retry_client.credits.get()

    error = excinfo.value
    assert error.status_code == 404
    assert error.code == "not_found"
    assert error.message == "No food has id 999999999."
    assert error.args[0] == "No food has id 999999999."
    assert error.docs_url == DOCS_URL
    assert error.request_id == "req_01J8Z4K3"
    assert isinstance(error.response, httpx.Response)
    assert error.response.status_code == 404
    assert error.body == body
    assert str(error) == "No food has id 999999999. (status 404, code not_found)"


def test_html_gateway_body_is_parsed_without_raising(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Survive an HTML error page from a proxy that answered instead of the API."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(502, html=HTML_GATEWAY_BODY),
    )

    with pytest.raises(InternalServerError) as excinfo:
        no_retry_client.credits.get()

    error = excinfo.value
    assert error.status_code == 502
    assert error.code is None
    assert error.docs_url is None
    assert error.body == HTML_GATEWAY_BODY
    assert error.message.startswith("<html>")
    # The message is truncated so an error page cannot flood a log line.
    assert len(error.message) <= 200
    assert str(error).endswith("(status 502)")


def test_empty_error_body_is_parsed_without_raising(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Fall back to a status-only message when the response carries no body at all."""
    respx_mock.get(CREDITS_URL).mock(return_value=httpx.Response(503, content=b""))

    with pytest.raises(InternalServerError) as excinfo:
        no_retry_client.credits.get()

    error = excinfo.value
    assert error.message == "HTTP 503"
    assert error.code is None
    assert error.docs_url is None
    assert error.body is None


def test_non_object_json_error_body_is_parsed_without_raising(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Treat valid JSON that is not an object as opaque text rather than an envelope."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(400, json=["unexpected", "shape"]),
    )

    with pytest.raises(BadRequestError) as excinfo:
        no_retry_client.credits.get()

    assert excinfo.value.code is None
    assert excinfo.value.docs_url is None
    assert excinfo.value.message.startswith("[")
    assert "unexpected" in excinfo.value.message


def test_unmapped_status_yields_a_plain_status_error(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Return a usable typed error for a status this release has no subclass for."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(
            422,
            json={
                "message": "The request could not be processed.",
                "code": "unprocessable_entity",
                "docs_url": DOCS_URL,
            },
        ),
    )

    with pytest.raises(APIStatusError) as excinfo:
        no_retry_client.credits.get()

    # Not an approximation to some neighbouring class: the base class itself, still carrying the
    # status and the code so the caller can decide what to do.
    assert type(excinfo.value) is APIStatusError
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "unprocessable_entity"


def test_request_id_comes_from_the_response_header(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Read ``request_id`` from ``x-request-id`` when the API sends it."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(
            500,
            json=error_body("internal_error"),
            headers={"x-request-id": "req_7f3a9c21"},
        ),
    )

    with pytest.raises(InternalServerError) as excinfo:
        no_retry_client.credits.get()

    assert excinfo.value.request_id == "req_7f3a9c21"


def test_request_id_is_none_when_the_header_is_absent(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Leave ``request_id`` unset rather than inventing one."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(500, json=error_body("internal_error")),
    )

    with pytest.raises(InternalServerError) as excinfo:
        no_retry_client.credits.get()

    assert excinfo.value.request_id is None


def test_rate_limit_error_parses_retry_after(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Expose the server's ``Retry-After`` on the raised rate-limit error."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(
            429,
            json=error_body("rate_limited"),
            headers={"retry-after": "42"},
        ),
    )

    with pytest.raises(RateLimitError) as excinfo:
        no_retry_client.credits.get()

    assert excinfo.value.retry_after == 42.0


@pytest.mark.parametrize(
    ("status", "code", "expected_class"),
    [
        (503, "service_unavailable", InternalServerError),
        (400, "invalid_request", BadRequestError),
        (429, "credit_limit_exceeded", CreditLimitExceededError),
    ],
    ids=["503", "400", "credit-limit"],
)
def test_retry_after_is_read_on_every_status_class(
    no_retry_client: January,
    respx_mock: respx.MockRouter,
    status: int,
    code: str,
    expected_class: type[APIStatusError],
) -> None:
    """Expose the header wherever it arrives, not on ``RateLimitError`` alone.

    A 503 carrying ``Retry-After: 600`` is telling the caller something just as actionable as a
    429 does, and the value used to be reachable only by reading ``e.response.headers`` by hand.
    """
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(status, json=error_body(code), headers={"retry-after": "600"}),
    )

    with pytest.raises(expected_class) as excinfo:
        no_retry_client.credits.get()

    assert excinfo.value.retry_after == 600.0


def test_retry_after_is_none_on_a_status_error_without_the_header(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """The attribute is always present, so no caller has to guard the lookup with hasattr."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(500, json=error_body("internal_error")),
    )

    with pytest.raises(InternalServerError) as excinfo:
        no_retry_client.credits.get()

    assert excinfo.value.retry_after is None


def test_a_huge_json_message_is_truncated_in_the_exception_string(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Bound ``str(error)`` however long the API's own ``message`` is, keeping the full text.

    The 200-character bound applied only to non-JSON bodies, so a two-million-character ``message``
    - a stack trace or a validation report echoed back by an upstream service - produced a
    two-million-character exception string that reached every log line and traceback.
    """
    enormous = "x" * 2_000_000
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(400, json={"message": enormous, "code": "invalid_request"}),
    )

    with pytest.raises(BadRequestError) as excinfo:
        no_retry_client.credits.get()

    error = excinfo.value
    assert len(str(error)) < 500
    assert "truncated" in error.message
    assert ".body" in error.message
    assert error.code == "invalid_request"
    # Nothing is lost: the untruncated value is still there for anyone who wants it.
    assert isinstance(error.body, dict)
    assert error.body["message"] == enormous


def test_a_short_json_message_is_left_exactly_as_it_arrived(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """The bound must not put a marker on the ordinary case, which is every real error body."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(400, json=error_body("invalid_request", message="No dice.")),
    )

    with pytest.raises(BadRequestError) as excinfo:
        no_retry_client.credits.get()

    assert excinfo.value.message == "No dice."


def test_rate_limit_error_retry_after_is_none_without_the_header(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Report ``None`` when the server did not say how long to wait."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(429, json=error_body("rate_limited")),
    )

    with pytest.raises(RateLimitError) as excinfo:
        no_retry_client.credits.get()

    assert excinfo.value.retry_after is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code", "expected"),
    STATUS_CODE_CASES,
    ids=[f"{status}-{code}" for status, code, _ in STATUS_CODE_CASES],
)
async def test_async_client_maps_failures_identically(
    no_retry_async_client: AsyncJanuary,
    respx_mock: respx.MockRouter,
    status: int,
    code: str,
    expected: type[APIStatusError],
) -> None:
    """Raise the same class from the asynchronous client as from the synchronous one."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(status, json=error_body(code)),
    )

    with pytest.raises(APIStatusError) as excinfo:
        await no_retry_async_client.credits.get()

    assert type(excinfo.value) is expected
    assert excinfo.value.status_code == status
    assert excinfo.value.code == code


# A response the SDK itself cannot make sense of, as opposed to one the API deliberately failed
# with. Everything below is a *success* status on the wire: the checks here are about the SDK
# keeping its own promises - that a method annotated to return a model returns one or raises, and
# that whatever it raises is still a JanuaryError.


def test_redirect_is_reported_rather_than_treated_as_a_success(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Refuse a 3xx instead of decoding it, and name what to fix."""
    # The API declares no 3xx on any operation and httpx does not follow redirects, so a redirect
    # means the request went somewhere other than the API - overwhelmingly a base_url with the
    # wrong scheme, where an http:// origin bounces to https://. Treating a redirect as a success
    # meant its empty body became a None handed back through a `-> CreditsResponse` annotation, and
    # the caller saw an AttributeError somewhere unrelated with nothing pointing at the cause.
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(
            301, headers={"location": "https://elsewhere.test/v1.2/credits"}
        ),
    )

    with pytest.raises(JanuaryError) as excinfo:
        no_retry_client.credits.get()

    message = str(excinfo.value)
    assert "301" in message
    assert "https://elsewhere.test/v1.2/credits" in message
    assert BASE_URL in message


def test_redirect_without_a_location_still_names_the_configured_origin(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Report a redirect that named no target, since base_url is still the thing to check."""
    respx_mock.get(CREDITS_URL).mock(return_value=httpx.Response(302))

    with pytest.raises(JanuaryError) as excinfo:
        no_retry_client.credits.get()

    assert BASE_URL in str(excinfo.value)


@pytest.mark.parametrize(
    ("status", "content"),
    [(200, b""), (200, b"\r\n  \n"), (204, b"")],
    ids=["empty-200", "whitespace-200", "204"],
)
def test_empty_success_body_is_reported_where_a_model_was_expected(
    no_retry_client: January, respx_mock: respx.MockRouter, status: int, content: bytes
) -> None:
    """Raise rather than return ``None`` from a method annotated to return a model."""
    # Seventeen of the eighteen operations declare a body. Handing back None from any of them
    # breaks the signature the SDK advertises and that `cast()` in the resource layer hides from
    # mypy, so the caller gets an AttributeError far from the cause. Only the operation's own
    # declaration - cast_to being None - may produce a None, which is why the status is not
    # consulted here: a 204 arriving on a model-declaring operation is just as wrong as an
    # empty 200 from a proxy that gave up.
    respx_mock.get(CREDITS_URL).mock(return_value=httpx.Response(status, content=content))

    with pytest.raises(JanuaryError) as excinfo:
        no_retry_client.credits.get()

    message = str(excinfo.value)
    assert "empty body" in message
    assert "CreditsResponse" in message


def test_no_content_is_still_the_answer_where_the_operation_returns_nothing(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Leave the one genuinely body-less operation alone."""
    route = respx_mock.delete(f"{BASE_URL}/v1.2/auth/client-tokens").mock(
        return_value=httpx.Response(204),
    )

    no_retry_client.auth.revoke_client_tokens("acme-user-8271")

    assert route.call_count == 1


def test_unexpected_response_shape_stays_inside_the_january_hierarchy(
    no_retry_client: January, respx_mock: respx.MockRouter
) -> None:
    """Wrap a pydantic failure so ``except JanuaryError`` really does cover every failure."""
    # A 200 carrying valid JSON of the wrong shape - server schema drift, or a proxy answering with
    # an envelope of its own - used to escape as a raw pydantic.ValidationError, which is not a
    # JanuaryError and so fell straight through the handler the README recommends. The sibling case
    # one branch away (a body that is not JSON at all) was already reported properly, which is what
    # made the gap an oversight rather than a decision.
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(200, json={"status": "queued", "message": "try later"}),
    )

    with pytest.raises(APIResponseValidationError) as excinfo:
        no_retry_client.credits.get()

    assert isinstance(excinfo.value, JanuaryError)
    assert excinfo.value.status_code == 200
    assert "CreditsResponse" in str(excinfo.value)
    # The field-level detail stays reachable rather than being thrown away.
    assert isinstance(excinfo.value.__cause__, ValidationError)


def test_use_after_close_is_reported_as_a_january_error(make_client: ClientFactory) -> None:
    """Explain a closed client instead of letting httpx raise a bare ``RuntimeError``."""
    # httpx raises RuntimeError, not HTTPError, so the send loop's own except clause never saw it
    # and it escaped the hierarchy the SDK documents.
    client = make_client()
    client.close()

    with pytest.raises(JanuaryError) as excinfo:
        client.credits.get()

    assert "closed" in str(excinfo.value)


def test_close_does_not_disable_a_client_whose_http_client_is_supplied(
    respx_mock: respx.MockRouter,
) -> None:
    """Keep a caller-owned transport usable, since ``close()`` deliberately does not close it."""
    # The guard above must key off the transport's real state rather than the SDK's own bookkeeping
    # flag, or it would break the documented promise that an httpx client you passed in is yours.
    respx_mock.get(CREDITS_URL).mock(return_value=httpx.Response(200, json=CREDITS_PAYLOAD))
    with httpx.Client() as http_client:
        client = January("sk-test-key", base_url=BASE_URL, http_client=http_client)
        client.close()

        assert client.credits.get().plan == "free"


LEAK_KEY = "sk-live-notarealkey-0011223344556677"


@pytest.mark.parametrize(
    "outcome",
    [
        httpx.Response(429, json=error_body("rate_limited")),
        httpx.ReadTimeout("timed out waiting for the response"),
        httpx.ConnectError("connection refused"),
    ],
    ids=["status-error", "read-timeout", "connect-error"],
)
def test_the_api_key_never_reaches_a_frame_local_on_a_traceback(
    respx_mock: respx.MockRouter, outcome: httpx.Response | Exception
) -> None:
    """Keep the credential off every SDK frame an exception carries."""
    # repr() redacting the key on the client covers only `self`. The send loop used to bind the
    # assembled headers - Authorization included, in a plain dict - to a local on the very frame
    # that raises, so anything that captures frame locals (pytest --showlocals, an error reporter
    # shipping the traceback to a third party) read the live key straight out of it on an ordinary
    # 429 or timeout. Building the headers inline is what keeps them off the frame, and this is the
    # property that matters: repr redaction cannot help a plain dict that a frame still holds.
    respx_mock.get(CREDITS_URL).mock(
        return_value=outcome if isinstance(outcome, httpx.Response) else None,
        side_effect=None if isinstance(outcome, httpx.Response) else outcome,
    )

    with (
        January(LEAK_KEY, base_url=BASE_URL, max_retries=0) as client,
        pytest.raises(JanuaryError) as excinfo,
    ):
        client.credits.get()

    assert _sdk_frames_holding(excinfo.value, LEAK_KEY) == []


@pytest.mark.anyio
async def test_the_api_key_never_reaches_a_frame_local_asynchronously(
    respx_mock: respx.MockRouter,
) -> None:
    """Keep the credential off the asynchronous send loop's frames as well."""
    respx_mock.get(CREDITS_URL).mock(
        side_effect=httpx.ReadTimeout("timed out waiting for the response"),
    )

    async with AsyncJanuary(LEAK_KEY, base_url=BASE_URL, max_retries=0) as client:
        with pytest.raises(JanuaryError) as excinfo:
            await client.credits.get()

    assert _sdk_frames_holding(excinfo.value, LEAK_KEY) == []


def _sdk_frames_holding(exc: BaseException, needle: str) -> list[str]:
    """Name every SDK frame on the traceback whose locals render ``needle``.

    Only frames inside ``january_ai`` are considered: a test or an application is free to hold its
    own key in a variable, and the SDK cannot and should not police that. What it can promise is
    that its own frames do not.
    """
    found: list[str] = []
    traceback = exc.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if f"{os.sep}january_ai{os.sep}" in frame.f_code.co_filename:
            for name, value in frame.f_locals.items():
                try:
                    rendered = repr(value)
                except Exception:  # pragma: no cover - a hostile __repr__ is not the point here
                    continue
                if needle in rendered:
                    found.append(f"{frame.f_code.co_name}.{name}")
        traceback = traceback.tb_next
    return found


@pytest.mark.anyio
async def test_async_redirect_is_reported_rather_than_treated_as_a_success(
    no_retry_async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Refuse a 3xx on the asynchronous client too."""
    respx_mock.get(CREDITS_URL).mock(
        return_value=httpx.Response(307, headers={"location": "https://elsewhere.test/"}),
    )

    with pytest.raises(JanuaryError) as excinfo:
        await no_retry_async_client.credits.get()

    assert "307" in str(excinfo.value)


@pytest.mark.anyio
async def test_async_empty_success_body_is_reported(
    no_retry_async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Report an empty success body asynchronously, rather than returning ``None``."""
    respx_mock.get(CREDITS_URL).mock(return_value=httpx.Response(200, content=b""))

    with pytest.raises(JanuaryError) as excinfo:
        await no_retry_async_client.credits.get()

    assert "empty body" in str(excinfo.value)


@pytest.mark.anyio
async def test_async_unexpected_response_shape_stays_inside_the_january_hierarchy(
    no_retry_async_client: AsyncJanuary, respx_mock: respx.MockRouter
) -> None:
    """Wrap a pydantic failure on the asynchronous client too."""
    respx_mock.get(CREDITS_URL).mock(return_value=httpx.Response(200, json={"plan": "free"}))

    with pytest.raises(APIResponseValidationError):
        await no_retry_async_client.credits.get()


@pytest.mark.anyio
async def test_async_use_after_close_is_reported_as_a_january_error(
    make_async_client: AsyncClientFactory,
) -> None:
    """Explain a closed asynchronous client rather than letting httpx raise ``RuntimeError``."""
    client = make_async_client()
    await client.aclose()

    with pytest.raises(JanuaryError) as excinfo:
        await client.credits.get()

    assert "closed" in str(excinfo.value)
