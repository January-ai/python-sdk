# Security Policy

## Reporting a vulnerability

Report security issues privately to **[security@january.ai](mailto:security@january.ai)**. Please do
not open a public GitHub issue, a pull request, or a Discord message for anything that could be
exploited before a fix ships.

Include whatever you have:

- the SDK version (`python -c "import january_ai; print(january_ai.__version__)"`), Python version,
  and operating system;
- what the issue lets an attacker do, and what access they need to do it;
- a minimal reproduction, ideally against a local or staging `base_url` rather than production;
- any workaround you have found.

**Never include a real API key, client token, or end-user data in a report.** Redact them, and if
you believe a credential has been exposed, rotate it in the
[Developer Dashboard](https://dashboard.january.ai) before writing to us.

What to expect:

| Stage | Target |
| --- | --- |
| Acknowledgement of your report | 2 business days |
| Initial assessment and severity | 5 business days |
| Fix released, or a dated plan for one | 30 days for high and critical severity |

We will keep you updated while we work, and credit you in the release notes and the changelog entry
unless you would rather stay anonymous. Please give us a chance to ship a fix before disclosing
publicly.

For a vulnerability in the January AI **API itself** rather than this SDK, the same address is the
right one. For anything that is not a security issue, use
[support@january.ai](mailto:support@january.ai) or the
[issue tracker](https://github.com/January-ai/python-sdk/issues).

## Supported versions

Security fixes land on the latest minor release of the current major version. Older majors are
supported for six months after the release of their successor.

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

While the SDK is pre-1.0, "latest minor" means the newest `0.x` release. Pin a compatible range
(`january-ai>=0.1,<0.2`) rather than an exact version so a patched release can reach you.

## Handling API keys

**An `sk-` key authenticates your entire account.** It is not scoped to one end user, one endpoint,
or one device. Treat it the way you treat a database password.

- Load it from the environment (`JANUARY_API_KEY`) or a secret manager. Never commit it, never bake
  it into a container image, never paste it into an issue or a log line.
- Use it only where you control the machine: your backend, a server-side job, your own laptop.
- Rotate it in the [Developer Dashboard](https://dashboard.january.ai) if it is ever exposed. The
  full value is shown once, at creation, and cannot be retrieved afterwards.

**Never ship an `sk-` key inside a mobile app, a desktop app, or browser JavaScript.** Anything
distributed to a device can be decompiled, inspected, or proxied, so the key becomes readable by
anyone who has a copy of your app.

### The client-token pattern

Mint a short-lived, single-user credential on your backend and relay it to the device:

```python
# Backend, authenticated with your sk- key, behind your own login.
token = client.auth.create_client_token(
    "user-1042",
    scopes=["foods:read", "food_scans:write"],
    ttl_seconds=1800,
)
return {"token": token.token, "expires_in": token.expires_in}
```

A client token (`ct-...`):

- is **bound to exactly one end user**, so it can only ever touch that user's data;
- carries **only the scopes you grant it** - grant the narrowest set the screen needs, not the
  default full set;
- **expires within two hours** (300 to 7200 seconds, defaulting to 1800);
- is **returned exactly once**, stored only as a hash, and can never be retrieved again.

The device treats it as an ordinary key (`January(relayed_token)`) and calls the API directly, with
no proxy of your own in the request path. On a `401`, the device asks your backend for a fresh token
and retries once. Prefer `expires_in` over `expires_at` when scheduling a refresh on a device: a
wrong device clock makes an absolute timestamp wrong with it.

When a device is lost or an account is deleted, revoke everything that user holds:

```python
client.auth.revoke_client_tokens("user-1042")
```

Revocation takes effect within 60 seconds, the authentication cache window, so also stop trusting
the user in your own app for an immediate cut-off.

### What the SDK does on your behalf

- The `Authorization` header, the API key, request bodies, and query strings are **never logged**.
  The `january_ai` logger emits retry decisions only.
- `repr()` on a client redacts the credential to its kind - `'sk-***'` or `'ct-***'` - so a client
  in a traceback, a notebook transcript, or a log line does not leak it.
- **No SDK frame holds the credential.** The assembled request headers are never bound to a local
  variable on the frame that raises, so a tool that captures frame locals - `pytest --showlocals`,
  or an error reporter shipping tracebacks to a third-party service - cannot read the key out of a
  timeout or a `429`. One place still carries it, as in every httpx-based SDK:
  `APIStatusError.response.request.headers` is the live request, `Authorization` included, so do not
  serialize that wholesale into a log or a bug report.
- Both `auth` methods require your `sk-` key; a client token cannot mint or revoke tokens.

## Image privacy

**Image preprocessing strips EXIF metadata, including GPS coordinates.** When `prepare_image` (or
`food_scans.scan_photo` with the default `preprocess=True`) re-encodes a photo, it writes no
metadata back, so the location, camera serial number, and capture timestamp a phone embedded in the
file do not leave your process.

Two cases where that does not apply, both deliberate:

- **`preprocess=False`** passes the original bytes through untouched, metadata included. Use it only
  for images you know carry nothing sensitive.
- **A photo already small, upright, and compliant** is sent as-is rather than re-encoded, which
  preserves whatever metadata it carries. Photos straight from a phone camera are neither small
  enough nor free of orientation data to take that path, but a thumbnail your own pipeline produced
  might be. Strip metadata upstream if that matters to you.

**An http(s) URL or a `data:` URI is forwarded byte for byte, whatever `preprocess` says.** Nothing
is decoded, and nothing is stripped. A URL you hand the API must be publicly fetchable, since
January downloads it server-side - do not put an authenticated or otherwise private URL there.

**A `str` that is not an http(s) URL or a `data:` URI is read from the local filesystem.** That is
the documented path form, and it is the mirror image of the warning above: **never pass a string
that came from an end user.** Doing so lets them name a file on your server, and any decodable
image at that path is uploaded to January - so if you accept image references from users, validate
them yourself, or pass `bytes` you fetched under your own rules. Only JPEG, PNG, WEBP, and GIF get
that far (anything else is refused at the decode), and other URI schemes such as `file://` are
rejected outright rather than falling through to a path, but neither is a substitute for treating
the parameter as trusted input.

January is SOC 2 Type II certified and follows HIPAA-aligned practices; a BAA and Zero Data
Retention are available. Contact [support@january.ai](mailto:support@january.ai) about either.
