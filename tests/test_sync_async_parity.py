"""The drift guard: ``AsyncJanuary`` must stay a mechanical mirror of ``January``.

The SDK promises that porting between the two clients is a matter of adding ``await``, and that
promise is easy to break by accident - a method added to one class, a keyword renamed on one side,
a default that drifts. Nothing in the type checker catches any of it, because the two classes are
unrelated. So this module compares them by introspection instead: same resources, same methods, and
for every method the same parameter names, kinds, defaults, and annotations.

Annotations are compared as the strings they are. Every module in the SDK begins with
``from __future__ import annotations``, so a signature carries source text rather than objects, and
that text is exactly what a reader of the two classes is comparing when they check for drift.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator

import pytest

from january_ai import AsyncJanuary, January

EXPECTED_RESOURCES = frozenset(
    {"auth", "credits", "foods", "restaurants", "food_scans", "food_logs", "glucose"}
)

# The 18 operations the /v1.2 API documents, counted across all seven resources.
EXPECTED_OPERATION_COUNT = 18


@pytest.fixture
def sync_client() -> Iterator[January]:
    """A synchronous client built only to be introspected; it never sends anything."""
    client = January("sk-test")
    yield client
    client.close()


@pytest.fixture
def async_client_for_introspection() -> AsyncJanuary:
    """An asynchronous client built only to be introspected.

    It is never awaited and never closed: nothing here opens a connection, and ``aclose`` would
    need an event loop this module has no other use for.
    """
    return AsyncJanuary("sk-test")


def _resource_names(client: object) -> set[str]:
    """Return the public attributes a client instance carries, which are its resource groups."""
    return {name for name in vars(client) if not name.startswith("_")}


def _method_names(resource: object) -> set[str]:
    """Return the public callables a resource exposes, which are its API operations."""
    return {
        name
        for name in dir(resource)
        if not name.startswith("_") and callable(getattr(resource, name))
    }


def test_both_clients_expose_the_same_resources(
    sync_client: January, async_client_for_introspection: AsyncJanuary
) -> None:
    """Expose one identical set of resource attributes, and exactly the documented seven."""
    sync_resources = _resource_names(sync_client)
    async_resources = _resource_names(async_client_for_introspection)

    assert sync_resources == async_resources
    assert sync_resources == EXPECTED_RESOURCES


def test_each_resource_pair_exposes_the_same_methods(
    sync_client: January, async_client_for_introspection: AsyncJanuary
) -> None:
    """Expose the same operation names on every resource pair, adding up to the documented 18."""
    total = 0

    for name in sorted(EXPECTED_RESOURCES):
        sync_methods = _method_names(getattr(sync_client, name))
        async_methods = _method_names(getattr(async_client_for_introspection, name))

        assert sync_methods == async_methods, f"{name}: operations differ between the clients"
        assert sync_methods, f"{name}: exposes no operations at all"
        total += len(sync_methods)

    assert total == EXPECTED_OPERATION_COUNT


def test_every_method_signature_matches(
    sync_client: January, async_client_for_introspection: AsyncJanuary
) -> None:
    """Compare parameter names, kinds, defaults, and annotations one operation at a time."""
    for resource_name in sorted(EXPECTED_RESOURCES):
        sync_resource = getattr(sync_client, resource_name)
        async_resource = getattr(async_client_for_introspection, resource_name)

        for method_name in sorted(_method_names(sync_resource)):
            where = f"{resource_name}.{method_name}"
            sync_signature = inspect.signature(getattr(sync_resource, method_name))
            async_signature = inspect.signature(getattr(async_resource, method_name))

            assert list(sync_signature.parameters) == list(async_signature.parameters), (
                f"{where}: parameter names or their order differ"
            )

            for parameter_name, sync_parameter in sync_signature.parameters.items():
                async_parameter = async_signature.parameters[parameter_name]
                assert sync_parameter.kind == async_parameter.kind, (
                    f"{where}: {parameter_name} is passed differently"
                )
                assert sync_parameter.default == async_parameter.default, (
                    f"{where}: {parameter_name} has a different default"
                )
                assert sync_parameter.annotation == async_parameter.annotation, (
                    f"{where}: {parameter_name} is annotated differently"
                )

            assert sync_signature.return_annotation == async_signature.return_annotation, (
                f"{where}: returns a different type"
            )


def test_every_method_annotation_text_matches(
    sync_client: January, async_client_for_introspection: AsyncJanuary
) -> None:
    """Compare the raw annotation source text, which is what a reader diffs the two classes by."""
    for resource_name in sorted(EXPECTED_RESOURCES):
        sync_resource = getattr(sync_client, resource_name)
        async_resource = getattr(async_client_for_introspection, resource_name)

        for method_name in sorted(_method_names(sync_resource)):
            sync_annotations = getattr(sync_resource, method_name).__annotations__
            async_annotations = getattr(async_resource, method_name).__annotations__
            assert sync_annotations == async_annotations, (
                f"{resource_name}.{method_name}: annotations differ"
            )


def test_async_methods_are_coroutines_and_sync_methods_are_not(
    sync_client: January, async_client_for_introspection: AsyncJanuary
) -> None:
    """Keep the one difference that is meant to exist: every async operation is awaitable."""
    for resource_name in sorted(EXPECTED_RESOURCES):
        sync_resource = getattr(sync_client, resource_name)
        async_resource = getattr(async_client_for_introspection, resource_name)

        for method_name in sorted(_method_names(sync_resource)):
            where = f"{resource_name}.{method_name}"
            assert inspect.iscoroutinefunction(getattr(async_resource, method_name)), (
                f"{where}: the async client's method is not a coroutine function"
            )
            assert not inspect.iscoroutinefunction(getattr(sync_resource, method_name)), (
                f"{where}: the sync client's method is a coroutine function"
            )


def test_the_two_constructors_take_the_same_arguments() -> None:
    """Keep the constructors in step too, apart from the HTTP client each one accepts.

    ``http_client`` is annotated ``httpx.Client`` on one and ``httpx.AsyncClient`` on the other,
    which is the point of having two classes; everything else about it must still match.
    """
    sync_signature = inspect.signature(January.__init__)
    async_signature = inspect.signature(AsyncJanuary.__init__)

    assert list(sync_signature.parameters) == list(async_signature.parameters)

    for parameter_name, sync_parameter in sync_signature.parameters.items():
        async_parameter = async_signature.parameters[parameter_name]
        assert sync_parameter.kind == async_parameter.kind, parameter_name
        assert sync_parameter.default == async_parameter.default, parameter_name
        if parameter_name != "http_client":
            assert sync_parameter.annotation == async_parameter.annotation, parameter_name

    assert sync_signature.parameters["http_client"].annotation == "httpx.Client | None"
    assert async_signature.parameters["http_client"].annotation == "httpx.AsyncClient | None"
