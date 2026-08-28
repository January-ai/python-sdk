"""Base model that every January AI schema derives from."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class JanuaryModel(BaseModel):
    """Base class for every model in the SDK.

    Fields the SDK does not know about are kept rather than rejected, so a server that starts
    sending a new key does not break an older SDK release. Unknown keys are reachable through
    ``model_extra`` and normal attribute access.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True, protected_namespaces=())
