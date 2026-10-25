"""Formatting helpers that reproduce JavaScript's number and JSON output exactly.

The TypeScript and Python implementations must print the same strings (rates, failure
details, CLI JSON). Python's defaults differ from JavaScript's in small ways: ``round()``
rounds half to even, ``repr(1.0)`` is ``"1.0"``, exponent notation kicks in at different
magnitudes, and string lengths count code points rather than UTF-16 code units. Everything
user-visible goes through these helpers instead.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

__all__ = [
    "math_round",
    "number_to_string",
    "stringify",
    "to_fixed",
    "utf16_len",
    "utf16_truncate",
]


def math_round(value: float) -> int:
    """``Math.round``: rounds half up (towards positive infinity)."""
    return math.floor(value + 0.5)


def to_fixed(value: float, digits: int) -> str:
    """``Number.prototype.toFixed``: rounds the exact binary value half away from zero."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    quantum = Decimal(1).scaleb(-digits)
    text = format(abs(Decimal(value)).quantize(quantum, rounding=ROUND_HALF_UP), "f")
    return f"-{text}" if value < 0 else text


def number_to_string(value: float) -> str:
    """``String(number)``: shortest round-trip digits, JavaScript's exponent thresholds."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    digits, exponent = _shortest_digits(abs(value))
    # value = 0.d1d2...dk * 10^n with n = exponent + len(digits)
    k = len(digits)
    n = exponent + k
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    e = n - 1
    exp = f"e+{e}" if e >= 0 else f"e-{-e}"
    mantissa = digits[0] if k == 1 else digits[0] + "." + digits[1:]
    return sign + mantissa + exp


def _shortest_digits(value: float) -> tuple[str, int]:
    """Shortest decimal digits and exponent with ``value == int(digits) * 10**exponent``."""
    text = repr(value)
    mantissa, _, exp_text = text.partition("e")
    exponent = int(exp_text) if exp_text else 0
    whole, _, frac = mantissa.partition(".")
    if frac == "0":
        frac = ""
    digits = (whole + frac).lstrip("0")
    exponent -= len(frac)
    stripped = digits.rstrip("0")
    exponent += len(digits) - len(stripped)
    return stripped or "0", exponent


def utf16_len(text: str) -> int:
    """Length in UTF-16 code units, as JavaScript's ``string.length`` counts it."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def utf16_truncate(text: str, limit: int, keep: int) -> str:
    """``text.length > limit ? text.slice(0, keep) + "..." : text`` with UTF-16 lengths."""
    if utf16_len(text) <= limit:
        return text
    raw = text.encode("utf-16-le", "surrogatepass")[: keep * 2]
    return raw.decode("utf-16-le", "ignore") + "..."


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def stringify(value: Any, indent: int | None = None) -> str:
    """``JSON.stringify(value, null, indent)`` for JSON-like values.

    Floats print as JavaScript prints them (``1.0`` becomes ``1``), non-finite numbers become
    ``null``, tuples are arrays, and dict keys keep their insertion order.
    """
    return _stringify(value, indent, 0)


def _stringify(value: Any, indent: int | None, depth: int) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return number_to_string(value) if math.isfinite(value) else "null"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        items = [(str(k), v) for k, v in value.items()]
        if indent is None:
            body = ",".join(f"{_string(k)}:{_stringify(v, None, 0)}" for k, v in items)
            return "{" + body + "}"
        pad = " " * (indent * (depth + 1))
        inner = (f"{pad}{_string(k)}: {_stringify(v, indent, depth + 1)}" for k, v in items)
        return "{\n" + ",\n".join(inner) + "\n" + " " * (indent * depth) + "}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) == 0:
            return "[]"
        if indent is None:
            return "[" + ",".join(_stringify(v, None, 0) for v in value) + "]"
        pad = " " * (indent * (depth + 1))
        parts = (f"{pad}{_stringify(v, indent, depth + 1)}" for v in value)
        return "[\n" + ",\n".join(parts) + "\n" + " " * (indent * depth) + "]"
    raise TypeError(f"{type(value).__name__} is not JSON-serialisable")
