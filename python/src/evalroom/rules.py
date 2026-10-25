"""A small, generic rule engine for scoring an output against a golden case's expectations.

Rules address a value in the output with a dot path and apply one operator. :func:`custom`
covers anything the operators cannot express.

Regular expressions (``matches`` / ``notMatches``) are compiled with Python's :mod:`re`.
Patterns written in the common subset of JavaScript and Python syntax (character classes,
anchors, quantifiers, groups) behave the same in both implementations; the flags ``i``,
``m`` and ``s`` are honoured, ``g``, ``y``, ``u``, ``d`` and ``v`` are accepted and have no
effect.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard

from ._js import number_to_string, stringify, utf16_truncate
from ._serde import MISSING, Missing, optional, to_json
from .result import Result, err, is_record, ok

__all__ = [
    "RULE_OPS",
    "CustomCheckResult",
    "CustomRule",
    "PathResult",
    "PathRule",
    "Rule",
    "RuleFailure",
    "RuleOp",
    "ScoreResult",
    "contains_phrase",
    "custom",
    "deep_equal",
    "evaluate_rule",
    "get_path",
    "normalize_text",
    "parse_rules",
    "score_output",
]

RuleOp = Literal[
    "eq",
    "neq",
    "in",
    "notIn",
    "between",
    "contains",
    "notContains",
    "matches",
    "notMatches",
    "subsetOf",
    "exists",
]
"""A built-in operator name."""

RULE_OPS: tuple[RuleOp, ...] = (
    "eq",
    "neq",
    "in",
    "notIn",
    "between",
    "contains",
    "notContains",
    "matches",
    "notMatches",
    "subsetOf",
    "exists",
)
"""Every built-in operator."""


@dataclass(frozen=True, slots=True)
class PathRule:
    """A declarative, JSON-serialisable rule.

    Operators (``value`` in brackets), passing when the value at ``path``:

    - ``eq`` / ``neq`` (any): deep-equals / does not deep-equal ``value``;
    - ``in`` / ``notIn`` (list): is / is not deep-equal to one element;
    - ``between`` (``[min, max]``): is a number with ``min <= x <= max``;
    - ``contains`` / ``notContains`` (str or list of str): a string contains every / none of
      the phrases after :func:`normalize_text`; a list has every / none of them as an element;
    - ``matches`` / ``notMatches`` (regex source): is a string that matches / does not match;
    - ``subsetOf`` (list): is a list whose every element is in ``value``;
    - ``exists`` (bool, default ``True``): is present (``True``) or absent (``False``).

    A path that does not resolve fails every operator except ``exists``.
    """

    path: str
    """Dot path into the output, e.g. ``"reply"``, ``"actions.0"``, ``"meta.flags[1]"``.
    ``""`` or ``"$"`` is the root."""
    op: RuleOp
    """Operator."""
    value: Any = MISSING
    """Operand; :data:`~evalroom._serde.MISSING` when the rule has none."""
    flags: str | None = optional()
    """Regex flags for ``matches`` / ``notMatches``, e.g. ``"i"``."""
    code: str | None = optional()
    """Failure code reported when the rule fails. Default ``f"{op}({path})"``."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape: ``{"path", "op", "value"?, "flags"?, "code"?}``."""
        result: dict[str, Any] = to_json(self)
        return result


CustomCheckResult = bool | str
"""What a custom check returns: ``True`` passes; ``False`` or a string (the detail) fails."""


@dataclass(frozen=True, slots=True)
class CustomRule:
    """A rule implemented as a function. Not serialisable: supply it at scoring time."""

    code: str
    """Failure code reported when the rule fails."""
    check: Callable[[Any], CustomCheckResult]
    """The check. Exceptions are caught and reported as failures."""
    path: str | None = None
    """Optional path reported in the failure, for display only."""
    op: Literal["custom"] = "custom"
    """Discriminator."""


Rule = PathRule | CustomRule
"""Any rule :func:`score_output` accepts."""


@dataclass(frozen=True, slots=True)
class RuleFailure:
    """One failed rule."""

    code: str
    """The rule's code."""
    path: str
    """The rule's path (``""`` for custom rules without one)."""
    detail: str
    """Human-readable reason."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape: ``{"code", "path", "detail"}``."""
        return {"code": self.code, "path": self.path, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Output of :func:`score_output`."""

    passed: bool
    """``True`` when no rule failed."""
    failures: list[RuleFailure]
    """Every failed rule, in rule order."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape: ``{"passed", "failures"}``."""
        result: dict[str, Any] = to_json(self)
        return result


def custom(
    code: str, check: Callable[[Any], CustomCheckResult], path: str | None = None
) -> CustomRule:
    """Builds a :class:`CustomRule`."""
    return CustomRule(code=code, check=check, path=path)


def _diacritic(char: str) -> bool:
    return unicodedata.combining(char) != 0 or unicodedata.category(char) in ("Mn", "Sk")


def _word_char(char: str) -> bool:
    return unicodedata.category(char)[0] in ("L", "N")


def normalize_text(text: str) -> str:
    """Case- and whitespace-insensitive text folding used by ``contains`` / ``notContains``.

    Lower-cases, strips diacritics (NFKD), turns punctuation into spaces and collapses runs of
    whitespace. ``"Full  REFUND, guaranteed!"`` becomes ``"full refund guaranteed"``.
    """
    folded = unicodedata.normalize("NFKD", text.lower())
    kept = "".join(char for char in folded if not _diacritic(char))
    spaced = "".join(char if _word_char(char) or char.isspace() else " " for char in kept)
    return " ".join(spaced.split())


def contains_phrase(haystack: str, needle: str) -> bool:
    """``True`` when ``needle`` occurs in ``haystack`` as a whole phrase after normalisation.

    ``"refund"`` is found in ``"Your refund is on its way"`` but not in ``"refundable"``.
    """
    n = normalize_text(needle)
    if not n:
        return True
    return f" {n} " in f" {normalize_text(haystack)} "


_INDEX = re.compile(r"\[(\d+)\]")
_DIGITS = re.compile(r"[0-9]+")


def _split_path(path: str) -> list[str]:
    if path in ("", "$"):
        return []
    trimmed = path[2:] if path.startswith("$.") else path
    return [segment for segment in _INDEX.sub(r".\1", trimmed).split(".") if segment]


@dataclass(frozen=True, slots=True)
class PathResult:
    """Output of :func:`get_path`."""

    found: bool
    """Whether the path resolved."""
    value: Any
    """The value at the path (``None`` when not found)."""


def get_path(value: Any, path: str) -> PathResult:
    """Resolves a dot path. Numeric segments index lists; ``a[0]`` is accepted as ``a.0``."""
    current = value
    for segment in _split_path(path):
        if isinstance(current, list):
            if not _DIGITS.fullmatch(segment):
                return PathResult(False, None)
            index = int(segment)
            if index >= len(current):
                return PathResult(False, None)
            current = current[index]
        elif is_record(current):
            if segment not in current:
                return PathResult(False, None)
            current = current[segment]
        else:
            return PathResult(False, None)
    return PathResult(True, current)


def _is_number(value: object) -> TypeGuard[float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def deep_equal(a: Any, b: Any) -> bool:
    """Structural equality for JSON-like values (primitives, lists, dicts).

    Booleans never equal numbers, and NaN equals NaN, as in JavaScript's ``Object.is``.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) and _is_number(b):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return True
        return a == b
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)):
            return False
        return len(a) == len(b) and all(deep_equal(x, y) for x, y in zip(a, b, strict=True))
    if is_record(a) and is_record(b):
        if len(a) != len(b):
            return False
        return all(key in b and deep_equal(a[key], b[key]) for key in a)
    if a is MISSING or b is MISSING:
        return a is b
    if a is None or b is None:
        return a is b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


def show(value: Any) -> str:
    """``JSON.stringify(value)``, truncated to 80 characters; ``"undefined"`` for MISSING."""
    if isinstance(value, Missing):
        text = "undefined"
    elif isinstance(value, re.Pattern):
        text = "{}"
    else:
        try:
            text = stringify(value)
        except TypeError:
            text = str(value)
    return utf16_truncate(text, 80, 77)


def _phrases(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else [value]


def _element_matches(element: Any, wanted: Any) -> bool:
    if isinstance(element, str) and isinstance(wanted, str):
        return normalize_text(element) == normalize_text(wanted)
    return deep_equal(element, wanted)


_JS_FLAG_ORDER = "dgimsuvy"
_PY_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


@dataclass(frozen=True, slots=True)
class _Regex:
    pattern: re.Pattern[str]
    source: str
    flags: str


def _js_source(source: str) -> str:
    if source == "":
        return "(?:)"
    out: list[str] = []
    escaped = False
    in_class = False
    for char in source:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            out.append("\\")
        elif char == "\n":
            out.append("\\n")
            continue
        out.append(char)
    return "".join(out)


def _compile(source: str, flags: str | None) -> _Regex:
    flag_text = flags or ""
    seen: set[str] = set()
    py_flags = 0
    for flag in flag_text:
        if flag not in _JS_FLAG_ORDER or flag in seen:
            raise ValueError(f"Invalid flags supplied to RegExp constructor '{flag_text}'")
        seen.add(flag)
        py_flags |= _PY_FLAGS.get(flag, 0)
    try:
        compiled = re.compile(source, py_flags)
    except re.error as error:
        raise ValueError(f"Invalid regular expression: /{source}/{flag_text}: {error}") from error
    ordered = "".join(flag for flag in _JS_FLAG_ORDER if flag in seen)
    return _Regex(compiled, _js_source(source), ordered)


def _regex(rule: PathRule) -> _Regex:
    if isinstance(rule.value, re.Pattern):
        pattern: re.Pattern[str] = rule.value
        flags = ("i" if pattern.flags & re.IGNORECASE else "") + (
            "m" if pattern.flags & re.MULTILINE else ""
        )
        flags += "s" if pattern.flags & re.DOTALL else ""
        return _Regex(pattern, _js_source(pattern.pattern), flags)
    value = rule.value
    text = "undefined" if value is MISSING else _string_of(value)
    return _compile(text, rule.flags)


def _string_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, (bool, int, float)):
        return number_to_string(value)
    if isinstance(value, list):
        return ",".join("" if v is None else _string_of(v) for v in value)
    if is_record(value):
        return "[object Object]"
    return str(value)


def _check_path_rule(output: Any, rule: PathRule) -> str | None:
    resolved = get_path(output, rule.path)
    where = rule.path or "$"
    if rule.op == "exists":
        wanted = True if rule.value is MISSING else _truthy(rule.value)
        if resolved.found == wanted:
            return None
        if wanted:
            return f"{where} is missing"
        return f"{where} should be absent but is {show(resolved.value)}"
    if not resolved.found:
        return f"{where} is missing"
    actual = resolved.value
    op = rule.op
    if op == "eq":
        if deep_equal(actual, rule.value):
            return None
        return f"expected {show(rule.value)}, got {show(actual)}"
    if op == "neq":
        return f"must not equal {show(rule.value)}" if deep_equal(actual, rule.value) else None
    if op in ("in", "notIn"):
        options = list(rule.value) if isinstance(rule.value, list) else []
        hit = any(deep_equal(actual, option) for option in options)
        if op == "in":
            return None if hit else f"{show(actual)} is not one of {show(options)}"
        return f"{show(actual)} is one of the excluded values {show(options)}" if hit else None
    if op == "between":
        bounds = list(rule.value) if isinstance(rule.value, list) else []
        low = bounds[0] if len(bounds) > 0 else MISSING
        high = bounds[1] if len(bounds) > 1 else MISSING
        if not _is_number(actual) or not math.isfinite(actual):
            return f"expected a number, got {show(actual)}"
        if actual >= _to_number(low) and actual <= _to_number(high):
            return None
        return (
            f"{number_to_string(actual)} is outside "
            f"[{_string_of_bound(low)}, {_string_of_bound(high)}]"
        )
    if op in ("contains", "notContains"):
        wanted_list = _phrases(rule.value)
        if isinstance(actual, str):
            present = [
                isinstance(phrase, str) and contains_phrase(actual, phrase)
                for phrase in wanted_list
            ]
        elif isinstance(actual, list):
            present = [
                any(_element_matches(element, phrase) for element in actual)
                for phrase in wanted_list
            ]
        else:
            return f"expected a string or an array, got {show(actual)}"
        if op == "contains":
            absent = [phrase for phrase, hit in zip(wanted_list, present, strict=True) if not hit]
            return None if not absent else f"missing {', '.join(show(p) for p in absent)}"
        found = [phrase for phrase, hit in zip(wanted_list, present, strict=True) if hit]
        return None if not found else f"contains forbidden {', '.join(show(p) for p in found)}"
    if op in ("matches", "notMatches"):
        if not isinstance(actual, str):
            return f"expected a string, got {show(actual)}"
        regex = _regex(rule)
        hit = regex.pattern.search(actual) is not None
        if op == "matches":
            return None if hit else f"does not match /{regex.source}/{regex.flags}"
        return f"matches forbidden /{regex.source}/{regex.flags}" if hit else None
    if op == "subsetOf":
        if not isinstance(actual, list):
            return f"expected an array, got {show(actual)}"
        allowed = list(rule.value) if isinstance(rule.value, list) else []
        extra = [
            element
            for element in actual
            if not any(_element_matches(element, option) for option in allowed)
        ]
        return None if not extra else f"not allowed: {', '.join(show(e) for e in extra)}"
    return f"unknown operator {show(op)}"


def _truthy(value: Any) -> bool:
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value)


def _to_number(value: Any) -> float:
    if value is MISSING:
        return math.nan
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if _is_number(value):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip()) if value.strip() else 0.0
        except ValueError:
            return math.nan
    return math.nan


def _string_of_bound(value: Any) -> str:
    return "undefined" if value is MISSING else _string_of(value)


def evaluate_rule(output: Any, rule: Rule) -> RuleFailure | None:
    """Evaluates one rule. Returns the failure, or ``None`` when the rule passes."""
    if isinstance(rule, CustomRule):
        result: CustomCheckResult
        try:
            result = rule.check(output)
        except Exception as error:
            result = f"custom rule threw: {error}"
        if result is True:
            return None
        detail = result if isinstance(result, str) else "custom rule failed"
        return RuleFailure(code=rule.code, path=rule.path or "", detail=detail)
    detail_text = _check_path_rule(output, rule)
    if detail_text is None:
        return None
    code = rule.code if rule.code is not None else f"{rule.op}({rule.path or '$'})"
    return RuleFailure(code=code, path=rule.path, detail=detail_text)


def score_output(output: Any, rules: Sequence[Rule]) -> ScoreResult:
    """Scores an output against rules.

    Every rule is evaluated (no short-circuit) so the result lists every failure.
    """
    failures = [f for f in (evaluate_rule(output, rule) for rule in rules) if f is not None]
    return ScoreResult(passed=not failures, failures=failures)


def _js_type_string(value: object) -> bool:
    return isinstance(value, str)


def parse_rules(data: object) -> Result[list[PathRule], list[str]]:
    """Validates untrusted input (``expectedRules`` from a JSON file) as :class:`PathRule` list.

    Checks operator names and operand shapes and compiles every regex.
    """
    if not isinstance(data, list):
        return err(["rules must be an array"])
    issues: list[str] = []
    rules: list[PathRule] = []
    for i, raw in enumerate(data):
        at = f"rules[{i}]"
        if isinstance(raw, PathRule):
            raw = raw.to_dict()  # noqa: PLW2901 - accept already-typed rules
        if not is_record(raw):
            issues.append(f"{at} must be an object")
            continue
        before = len(issues)
        if not isinstance(raw.get("path"), str):
            issues.append(f"{at}.path must be a string")
        op = raw.get("op")
        has_value = "value" in raw
        value = raw.get("value", MISSING)
        if not isinstance(op, str) or op not in RULE_OPS:
            issues.append(f"{at}.op must be one of {', '.join(RULE_OPS)}")
        else:
            if op in ("in", "notIn", "subsetOf") and not isinstance(value, list):
                issues.append(f"{at}: {op} needs an array value")
            if op == "between":
                valid = (
                    isinstance(value, list)
                    and len(value) == 2
                    and all(_is_number(b) and math.isfinite(b) for b in value)
                    and value[0] <= value[1]
                )
                if not valid:
                    issues.append(f"{at}: between needs [min, max] numbers with min <= max")
            if op in ("contains", "notContains"):
                items = value if isinstance(value, list) else [value]
                if len(items) == 0 or not has_value:
                    issues.append(f"{at}: {op} needs a value")
                if any(isinstance(p, str) and normalize_text(p) == "" for p in items):
                    issues.append(f"{at}: {op} phrases must contain a letter or digit")
            if op in ("matches", "notMatches"):
                if not isinstance(value, str):
                    issues.append(f"{at}: {op} needs a regex source string")
                else:
                    flags = raw.get("flags")
                    try:
                        _compile(value, flags if isinstance(flags, str) else None)
                    except ValueError as error:
                        issues.append(f"{at}: invalid regex ({error})")
            if op == "exists" and has_value and not isinstance(value, bool):
                issues.append(f"{at}: exists takes an optional boolean")
        if "flags" in raw and not isinstance(raw["flags"], str):
            issues.append(f"{at}.flags must be a string")
        if "code" in raw and (not isinstance(raw["code"], str) or raw["code"] == ""):
            issues.append(f"{at}.code must be a non-empty string")
        if len(issues) == before and isinstance(op, str) and op in RULE_OPS:
            rules.append(
                PathRule(
                    path=raw["path"],
                    op=op,
                    value=value,
                    flags=raw["flags"] if isinstance(raw.get("flags"), str) else None,
                    code=raw["code"] if isinstance(raw.get("code"), str) else None,
                )
            )
    return err(issues) if issues else ok(rules)
