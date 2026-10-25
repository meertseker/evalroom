"""Conversion between Python value types and the on-disk JSON shapes.

Python code uses snake_case dataclasses; files and CLI JSON use the camelCase keys of the
TypeScript implementation, so files are interchangeable between the two. Every conversion
lives here.

Serialisation is generic: a dataclass field becomes its camelCase key; a field declared with
``optional()`` is omitted when it is ``None`` (TypeScript's optional property), every other
``None`` is written as ``null``; the :data:`MISSING` sentinel is always omitted.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

__all__ = ["MISSING", "Missing", "camel", "optional", "to_json"]


class Missing:
    """Type of :data:`MISSING`, the marker for an absent value (JavaScript's ``undefined``)."""

    _instance: Missing | None = None

    def __new__(cls) -> Missing:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False

    def __copy__(self) -> Missing:
        return self

    def __deepcopy__(self, memo: object) -> Missing:
        return self


MISSING: Final = Missing()
"""An absent value, distinct from ``None`` (JSON ``null``)."""

_OPTIONAL = "evalroom_optional"
_KEY = "evalroom_key"


def optional(default: Any = None, *, key: str | None = None) -> Any:
    """A dataclass field that is omitted from JSON when ``None``."""
    metadata: dict[str, Any] = {_OPTIONAL: True}
    if key is not None:
        metadata[_KEY] = key
    return dataclasses.field(default=default, metadata=metadata)


def renamed(key: str, **kwargs: Any) -> Any:
    """A dataclass field written under an explicit JSON key."""
    return dataclasses.field(metadata={_KEY: key}, **kwargs)


_CAMEL = re.compile(r"_([a-z0-9])")


def camel(name: str) -> str:
    """``rubric_version`` -> ``rubricVersion``."""
    return _CAMEL.sub(lambda match: match.group(1).upper(), name)


def to_json(value: Any) -> Any:
    """Converts dataclasses (recursively) into JSON-ready dicts, lists and primitives."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {}
        for f in dataclasses.fields(value):
            if not f.init and f.name.startswith("_"):
                continue
            item = getattr(value, f.name)
            if item is MISSING:
                continue
            if item is None and f.metadata.get(_OPTIONAL):
                continue
            out[f.metadata.get(_KEY, camel(f.name))] = to_json(item)
        return out
    if isinstance(value, Mapping):
        return {str(k): to_json(v) for k, v in value.items() if v is not MISSING}
    if isinstance(value, (str, bytes, bytearray)):
        return value
    if isinstance(value, Sequence):
        return [to_json(v) for v in value]
    if isinstance(value, re.Pattern):
        return value.pattern
    return value
