"""Assertions about the repository itself rather than about the library's runtime behaviour.

Two things here are checked into git and can regress without a single line of ``src`` changing, so
neither is covered by any other test in the suite.

The release workflow is the first. It publishes to PyPI, which is irreversible - a version can never
be re-uploaded - and it is triggered by a tag, which can be pushed onto any commit including one CI
never ran. It therefore has to run the whole gate itself before it builds anything, and these tests
pin that down against the YAML.

What the documentation claims about credits is the second. The SDK used to state flatly that every
successful ``/v1.2`` call except ``credits.get()`` costs a credit. Measured against the live API by
reading ``used_credits`` either side of each call, the two ``auth`` operations bill nothing either,
so the claim was wrong in the SDK's own docstrings, in its models' field descriptions, in the
README, and in an example's stated cost. These tests keep the corrected wording from silently
reverting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from january_ai.resources.credits import AsyncCredits, Credits
from january_ai.types import CreditsResponse

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
RELEASE_WORKFLOW: Final = REPO_ROOT / ".github" / "workflows" / "release.yml"
README: Final = REPO_ROOT / "README.md"
CLIENT_TOKEN_EXAMPLE: Final = REPO_ROOT / "examples" / "04_client_tokens.py"

# The floor from ``requires-python`` and the newest interpreter the classifiers claim. A release
# that has not been run on both has not been tested on the range it advertises.
REQUIRED_PYTHON_VERSIONS: Final = ("3.10", "3.14")

# The four commands that make up the project's gate, as CONTRIBUTING and ci.yml spell them.
REQUIRED_VERIFY_COMMANDS: Final = (
    "uv run ruff check .",
    "uv run ruff format --check .",
    "uv run mypy src tests examples",
    "uv run pytest",
)


def _read(path: Path) -> str:
    """Read a checked-in file, naming the missing one rather than raising a bare ``OSError``."""
    if not path.exists():
        raise RuntimeError(f"{path.relative_to(REPO_ROOT)} is missing from the repository")
    return path.read_text(encoding="utf-8")


def _workflow_jobs(text: str) -> dict[str, str]:
    """Split a workflow's ``jobs:`` mapping into one block of text per job.

    Parsed by indentation rather than with a YAML library, since the project has no YAML dependency
    and adding one to the locked dev environment to read four job names would cost more than it is
    worth. Job names sit at exactly two spaces of indentation under a top-level ``jobs:``.

    Args:
        text: The whole workflow file.

    Returns:
        Each job's name mapped to the lines beneath it, the header line included.
    """
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in text.splitlines():
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            in_jobs = line.startswith("jobs:")
            current = None
            continue
        if not in_jobs:
            continue
        header = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*", line)
        if header is not None:
            current = header.group(1)
            jobs[current] = [line]
        elif current is not None:
            jobs[current].append(line)
    return {name: "\n".join(lines) for name, lines in jobs.items()}


@pytest.fixture(scope="module")
def release_jobs() -> dict[str, str]:
    """The release workflow's jobs, keyed by name."""
    return _workflow_jobs(_read(RELEASE_WORKFLOW))


# --------------------------------------------------------------------------------------------
# The release workflow
# --------------------------------------------------------------------------------------------


def test_the_release_workflow_verifies_before_it_builds(release_jobs: dict[str, str]) -> None:
    """Refuse to publish from a workflow that does not run the suite first."""
    # The workflow used to go straight from a tag to `uv build` to a PyPI upload: it asserted that
    # the tag matched _version.py and that the wheel contained what it should, and ran neither ruff,
    # nor mypy, nor a single test. A `v*` tag on a red commit published it, permanently.
    assert "verify" in release_jobs, "release.yml has no verify job"
    assert "publish" in release_jobs

    # Whichever job publishes must sit downstream of verify, so a red suite blocks the upload.
    build = release_jobs["build"]
    assert re.search(r"^\s*needs:\s*verify\s*$", build, re.MULTILINE), (
        "the build job does not depend on verify"
    )
    assert re.search(r"^\s*needs:\s*build\s*$", release_jobs["publish"], re.MULTILINE)


@pytest.mark.parametrize("command", REQUIRED_VERIFY_COMMANDS)
def test_the_release_verification_runs_the_whole_gate(
    release_jobs: dict[str, str], command: str
) -> None:
    """Run lint, formatting, types, and tests before a release, exactly as CI does on a PR."""
    verify = release_jobs["verify"]
    assert f"run: {command}" in verify, f"the verify job never runs {command!r}"
    assert "uv sync --frozen" in verify


@pytest.mark.parametrize("version", REQUIRED_PYTHON_VERSIONS)
def test_the_release_verification_covers_the_supported_python_range(
    release_jobs: dict[str, str], version: str
) -> None:
    """Verify on both ends of the range the package's classifiers advertise."""
    # A single interpreter would miss exactly the failures a release is most exposed to: syntax and
    # typing that the floor rejects, and deprecations the newest version has started enforcing.
    assert f'"{version}"' in release_jobs["verify"]


def test_the_release_still_asserts_the_tag_matches_the_version(
    release_jobs: dict[str, str],
) -> None:
    """Keep the check that a tag names the version actually being built."""
    build = release_jobs["build"]
    assert "src/january_ai/_version.py" in build
    assert "GITHUB_REF_NAME" in build


def test_the_release_workflow_pins_its_actions(release_jobs: dict[str, str]) -> None:
    """Pin the toolchain, so an upstream change cannot alter what a release publishes."""
    verify = release_jobs["verify"]
    assert "astral-sh/setup-uv@v6" in verify
    assert 'version: "0.11.3"' in verify
    assert "actions/checkout@v4" in verify
    assert "enable-cache: true" in verify


# --------------------------------------------------------------------------------------------
# What the documentation claims about credits
# --------------------------------------------------------------------------------------------

# The absolute forms the SDK used to state, each of which is now false: minting and revoking a
# client token bill nothing.
RETIRED_CREDIT_CLAIMS: Final = (
    "One successful `/v1.2` call costs 1 credit",
    "Every successful v1.2 API call costs one credit",
    "One successful v1.2 API call costs 1 credit",
)


@pytest.mark.parametrize("claim", RETIRED_CREDIT_CLAIMS)
def test_the_unqualified_billing_claim_is_gone_from_the_documentation(claim: str) -> None:
    """Keep the flat "every call costs a credit" wording from coming back anywhere it lived."""
    sources = [
        _read(README),
        _read(CLIENT_TOKEN_EXAMPLE),
        Credits.__doc__ or "",
        AsyncCredits.__doc__ or "",
        str(CreditsResponse.model_fields["used_credits"].description),
    ]
    for source in sources:
        assert claim not in source


def test_the_credits_documentation_names_the_unbilled_operations() -> None:
    """State which operations were measured as free, in the docstrings a reader actually opens."""
    # Both clients, since a reader of the async one must not be told something different.
    for docstring in (
        Credits.__doc__ or "",
        Credits.get.__doc__ or "",
        AsyncCredits.get.__doc__ or "",
    ):
        assert "auth" in docstring
        assert "measured" in docstring.lower()


def test_the_readme_lists_the_unbilled_operations_as_measured_not_promised() -> None:
    """Say in the README both which calls are free and how that was established."""
    # The distinction matters: it was measured against the live API, not promised by the published
    # spec, so an integrator must not hard-code it into their own billing arithmetic.
    readme = _read(README)
    assert "client.auth.create_client_token(...)` | no" in readme
    assert "client.auth.revoke_client_tokens(...)` | no" in readme
    assert "client.credits.get()` | no" in readme
    assert "**measured**" in readme


def test_the_client_token_example_states_the_measured_cost() -> None:
    """Stop the example from charging the reader for two calls that bill nothing."""
    example = _read(CLIENT_TOKEN_EXAMPLE)
    assert "Cost: 3 credits" not in example
    assert "Cost: 1 credit" in example
