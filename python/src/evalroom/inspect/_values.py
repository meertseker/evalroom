"""Reading Inspect score values as pass/fail outcomes and as labels.

Pure functions with no Inspect import, so they are easy to test on their own. The mapping
follows Inspect's ``value_to_float`` conventions: ``C`` is correct, ``I`` incorrect, ``N``
no answer (incorrect), ``P`` partial credit, ``yes``/``true`` and ``no``/``false`` are
booleans, and numbers 1 and 0 are correct and incorrect. NaN is Inspect's "unscored" marker.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from .._js import number_to_string

__all__ = ["PARTIAL_POLICIES", "Partial", "excerpt", "label_of", "outcome_of"]

Partial = Literal["fail", "pass", "exclude"]
"""How partial credit (``P``, or a number strictly between 0 and 1) is counted."""
PARTIAL_POLICIES: tuple[Partial, ...] = ("fail", "pass", "exclude")

_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


class UnreadableValue(ValueError):
    """A score value that cannot be read as pass/fail (a list, a dict, an odd string)."""


def _partial(policy: Partial) -> bool | None:
    if policy == "pass":
        return True
    if policy == "fail":
        return False
    return None


def outcome_of(value: object, partial: Partial = "fail") -> bool | None:
    """``True`` (pass), ``False`` (fail) or ``None`` (excluded: unscored, or partial credit
    under ``partial="exclude"``).

    :raises UnreadableValue: for values that are not a grade, a boolean or a number in [0, 1].
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number):
            return None
        if number == 1:
            return True
        if number == 0:
            return False
        if 0 < number < 1:
            return _partial(partial)
        raise UnreadableValue(f"score value {number_to_string(number)} is not in [0, 1]")
    if isinstance(value, str):
        text = value.strip()
        upper = text.upper()
        if upper == "C":
            return True
        if upper in ("I", "N"):
            return False
        if upper == "P":
            return _partial(partial)
        lower = text.lower()
        if lower in ("yes", "true", "correct", "pass"):
            return True
        if lower in ("no", "false", "incorrect", "fail"):
            return False
        if _NUMBER.match(text):
            return outcome_of(float(text), partial)
        raise UnreadableValue(f'score value "{value}" is not a grade')
    raise UnreadableValue(f"score value of type {type(value).__name__} is not a grade")


def label_of(value: object, partial: Partial = "fail") -> str | None:
    """A grade label for agreement statistics: ``"C"``, ``"I"``, ``"N"`` or ``"P"``.

    Partial credit becomes ``"I"`` under ``partial="fail"``, ``"C"`` under ``"pass"``, and
    ``None`` (excluded) under ``"exclude"``. Unscored values (NaN) are ``None``. Other
    strings are kept as they are, so custom grade scales still produce a confusion matrix.
    """
    if isinstance(value, bool):
        return "C" if value else "I"
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number):
            return None
        if number == 1:
            return "C"
        if number == 0:
            return "I"
        if 0 < number < 1:
            return _partial_label(partial)
        return number_to_string(number)
    if isinstance(value, str):
        text = value.strip()
        upper = text.upper()
        if upper in ("C", "I", "N"):
            return upper
        if upper == "P":
            return _partial_label(partial)
        lower = text.lower()
        if lower in ("yes", "true", "correct", "pass"):
            return "C"
        if lower in ("no", "false", "incorrect", "fail"):
            return "I"
        if _NUMBER.match(text):
            return label_of(float(text), partial)
        return text
    raise UnreadableValue(f"score value of type {type(value).__name__} is not a grade")


def _partial_label(policy: Partial) -> str | None:
    if policy == "pass":
        return "C"
    if policy == "fail":
        return "I"
    return None


def excerpt(text: object, limit: int = 60) -> str:
    """A one-line excerpt: whitespace collapsed, cut at ``limit`` characters with ``…``."""
    if text is None:
        return ""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"
