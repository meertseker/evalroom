"""A tiny Result type.

Functions that can refuse for a domain reason (an incomplete item, an invalid rubric, a
self-activation) return a :data:`Result` instead of raising, so the caller sees every reason
at once. Narrow with ``isinstance(result, Ok)`` or on ``result.ok``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeGuard, TypeVar

__all__ = ["Err", "Ok", "Result", "err", "is_record", "ok"]

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    """Successful outcome carrying a value."""

    value: T

    @property
    def ok(self) -> Literal[True]:
        """Always ``True``."""
        return True


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    """Refused outcome carrying the reason(s)."""

    error: E

    @property
    def ok(self) -> Literal[False]:
        """Always ``False``."""
        return False


Result = Ok[T] | Err[E]
"""Either :class:`Ok` or :class:`Err`."""


def ok(value: T) -> Ok[T]:
    """Builds an :class:`Ok`."""
    return Ok(value)


def err(error: E) -> Err[E]:
    """Builds an :class:`Err`."""
    return Err(error)


def is_record(value: object) -> TypeGuard[dict[str, Any]]:
    """``True`` for a JSON object (a ``dict``)."""
    return isinstance(value, dict)
