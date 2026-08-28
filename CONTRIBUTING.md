# Contributing

Thanks for helping improve the January AI Python SDK. This document covers the setup, the checks CI
runs, and the two conventions that are easy to miss: sync/async parity and the OpenAPI spec as the
source of truth for models.

## Getting set up

The project uses [uv](https://docs.astral.sh/uv/) for dependency management and packaging.

```bash
git clone https://github.com/januaryai/python-sdk
cd python-sdk
uv sync
```

`uv sync` creates `.venv` and installs the runtime dependencies plus the `dev` group (pytest,
pytest-cov, respx, anyio's trio extra, mypy, ruff) from `uv.lock`. Nothing else is required; do not
install into a global environment.

## The checks

Run all four before opening a pull request. CI runs exactly these, so a clean local run means a
clean build.

```bash
uv run pytest             # tests
uv run ruff check .       # lint
uv run ruff format .      # format (CI runs --check)
uv run mypy src tests     # type-check, strict
```

- **Tests** never touch the network and never really sleep. HTTP is intercepted with
  [respx](https://lundberg.github.io/respx/), and the retry clock is a fake sleeper that records the
  delays it was asked for, so a test asserting backoff behaviour runs instantly. Image fixtures are
  generated with Pillow at test time - no binary files in the repository.
- **Lint and format** are ruff, at line length 100 with double quotes, over rules `E`, `F`, `W`,
  `I`, `UP`, `B`, `C4`, `SIM`, and `RUF`. Let `ruff format` do the formatting rather than arguing
  with it by hand.
- **Types** are checked with `mypy --strict` over both `src` and `tests`, with the pydantic plugin
  enabled. Test code is held to the same bar as library code. `Any` is not acceptable where a real
  type exists; if you genuinely need one, say why in a comment.

Coverage runs in CI as `uv run pytest --cov`. New behaviour needs a test, and a bug fix needs a test
that fails without the fix.

## Sync and async must stay identical

Every resource exists twice: `Foods` and `AsyncFoods`, `FoodLogs` and `AsyncFoodLogs`, and so on.
The pair must agree on **method names, parameter names, parameter order, defaults, and return
types**. The only difference between them is `async def` and `await`. A user porting code from one
client to the other should need to add `await` and nothing else.

This is why request construction lives in module-level `_..._spec(...)` builders that return a
`RequestSpec`, shared by both classes, and why the methods themselves are one-liners over
`self._client.send(...)`. Adding a parameter means editing the spec builder once and both method
signatures.

Concretely, when you touch a resource:

1. Change the `_..._spec` builder.
2. Change the sync method and the async method, identically.
3. Copy the docstring across, not a paraphrase of it.
4. Add tests for both. `tests/` uses anyio so the async tests run on asyncio and trio.

The same rule applies to `January` and `AsyncJanuary`: same constructor arguments, same attributes,
same behaviour, differing only in `close()` versus `aclose()` and the context-manager protocol.

## Models are transcribed from the OpenAPI spec

`src/january_ai/types/` is not hand-designed. Every model there is a transcription of a schema in
the January OpenAPI document, and the spec is the arbiter of any disagreement.

- **Names.** A model's name is the OpenAPI schema name with the `Dto` suffix removed, with no other
  changes. `FoodDto` is `Food`, `CreditsResponseDto` is `CreditsResponse`,
  `DeleteFoodLogResponseDto` is `DeleteFoodLogResponse`. A developer reading the API reference must
  find the identical name in the SDK.
- **Fields.** Names, types, required-versus-optional, and nullability come from the schema, not from
  a guess about what the server "probably" returns. Optional becomes `X | None = None`; required and
  nullable becomes `X | None` with no default. Use `int` for identifiers and counts, `float` for
  measured amounts, `datetime` and `date` for the corresponding string formats.
- **Docstrings.** Transcribe the substance of the schema and field descriptions - they are detailed
  and they are correct. Do not invent behaviour the spec does not describe, and do not narrate the
  code.
- **Enum-like response fields are typed `str`**, never a `Literal`. A value the server adds after a
  release must still parse. Request-side vocabularies do use `Literal`, in `types/shared.py`, where
  a closed set catches typos in your editor. `enum.Enum` is not used anywhere.
- **Every response model allows extra fields** (`extra="allow"` on `JanuaryModel`). That is the
  forward-compatibility guarantee promised in the README; do not tighten it.

If the API adds a field or an endpoint, update the spec first, then transcribe.

## Style

- `from __future__ import annotations` at the top of every module.
- Full type annotations on everything, including tests.
- Docstrings on every public class and method: imperative summary line, then `Args`/`Returns`/
  `Raises` where they earn their place.
- Private modules are underscore-prefixed. Anything not underscore-prefixed is public API and is
  covered by semantic versioning.
- Comments state non-obvious constraints - why a check has to happen in that order, why a Python
  version forces a workaround. They do not restate what the line does.
- No emoji, anywhere.

## Pull requests

- Branch from `main`, keep the change focused, and rebase rather than merge.
- Write the commit message as a sentence saying what changes and why.
- Add a line to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md) under `Added`, `Changed`,
  `Fixed`, `Deprecated`, or `Removed`, whenever the change is visible to a user of the SDK.
- Call out anything that changes public behaviour, however small. Pre-1.0 is not a licence to break
  people quietly.

Found a security issue? Do not open a pull request. See [SECURITY.md](SECURITY.md).
