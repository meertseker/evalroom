"""Rubrics: the versioned contract reviewers grade against.

A rubric declares its lenses and the error classes each lens may pick. Every review is
stamped with the rubric version, and agreement is only ever computed within one version.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard

from ._js import number_to_string
from ._serde import optional, to_json
from .result import Result, err, is_record, ok

__all__ = [
    "SEVERITIES",
    "VERDICTS",
    "ErrorClassDef",
    "Rubric",
    "RubricError",
    "Severity",
    "Verdict",
    "define_rubric",
    "error_classes_for_lens",
    "find_error_class",
    "is_severity",
    "is_verdict",
    "parse_rubric",
]

Verdict = Literal["PASS", "FAIL", "NEEDS_REVIEW"]
"""``PASS | FAIL | NEEDS_REVIEW``."""
VERDICTS: tuple[Verdict, ...] = ("PASS", "FAIL", "NEEDS_REVIEW")
"""The three verdicts a reviewer can give."""

Severity = Literal["low", "medium", "high"]
"""``low | medium | high``."""
SEVERITIES: tuple[Severity, ...] = ("low", "medium", "high")
"""Severities a failing review may carry."""


def is_verdict(value: object) -> TypeGuard[Verdict]:
    """``True`` when ``value`` is one of :data:`VERDICTS`."""
    return isinstance(value, str) and value in VERDICTS


def is_severity(value: object) -> TypeGuard[Severity]:
    """``True`` when ``value`` is one of :data:`SEVERITIES`."""
    return isinstance(value, str) and value in SEVERITIES


@dataclass(frozen=True, slots=True)
class ErrorClassDef:
    """One error class a reviewer may assign to a failing output."""

    code: str
    """Stable machine code, unique within the rubric, e.g. ``"WRONG_FACT"``."""
    label: str
    """Short human label."""
    lenses: tuple[str, ...]
    """Lenses that may pick this class. Must be declared in the rubric."""
    include: str
    """When to pick this class."""
    exclude: str
    """When not to pick it (and what to pick instead)."""
    good_example: str | None = optional()
    """An output that is fine with respect to this class."""
    bad_example: str | None = optional()
    """An output that shows this error."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape (camelCase keys)."""
        result: dict[str, Any] = to_json(self)
        return result


@dataclass(frozen=True, slots=True)
class Rubric:
    """A validated, immutable rubric. Create it with :func:`define_rubric`."""

    version: str
    """Version label. Bump it whenever an include/exclude rule changes meaning."""
    lenses: tuple[str, ...]
    """Reviewer perspectives. An item is complete when every lens has a current verdict."""
    error_classes: tuple[ErrorClassDef, ...]
    """Error classes reviewers may assign."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of ``rubric.json``."""
        result: dict[str, Any] = to_json(self)
        return result


class RubricError(ValueError):
    """Raised by :func:`define_rubric` for an invalid rubric. ``issues`` lists every problem."""

    issues: tuple[str, ...]

    def __init__(self, issues: Sequence[str]) -> None:
        super().__init__(f"Invalid rubric: {'; '.join(issues)}")
        self.issues = tuple(issues)


def _non_empty(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value.strip() != ""


def _show(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, (bool, int, float)):
        return number_to_string(value)
    return str(value)


def _collect_issues(data: object) -> list[str]:
    if not is_record(data):
        return ["rubric must be an object"]
    issues: list[str] = []
    if not _non_empty(data.get("version")):
        issues.append("version must be a non-empty string")
    lenses = data.get("lenses")
    declared: set[str] = set()
    if not isinstance(lenses, list) or len(lenses) == 0:
        issues.append("lenses must be a non-empty array")
    else:
        for i, lens in enumerate(lenses):
            if not _non_empty(lens):
                issues.append(f"lenses[{i}] must be a non-empty string")
            elif lens in declared:
                issues.append(f'lens "{lens}" is declared twice')
            else:
                declared.add(lens)
    classes = data.get("errorClasses")
    if not isinstance(classes, list):
        issues.append("errorClasses must be an array")
        return issues
    codes: set[str] = set()
    for i, entry in enumerate(classes):
        at = f"errorClasses[{i}]"
        if not is_record(entry):
            issues.append(f"{at} must be an object")
            continue
        code = entry.get("code")
        if not _non_empty(code):
            issues.append(f"{at}.code must be a non-empty string")
        elif code in codes:
            issues.append(f'error class code "{code}" is not unique')
        else:
            codes.add(code)
        name = f'error class "{code}"' if _non_empty(code) else at
        for key in ("label", "include", "exclude"):
            if not _non_empty(entry.get(key)):
                issues.append(f"{name}: {key} must be a non-empty string")
        for key in ("goodExample", "badExample"):
            if key in entry and not isinstance(entry[key], str):
                issues.append(f"{name}: {key} must be a string when present")
        entry_lenses = entry.get("lenses")
        if not isinstance(entry_lenses, list) or len(entry_lenses) == 0:
            issues.append(f"{name}: lenses must be a non-empty array")
        else:
            for lens in entry_lenses:
                if not isinstance(lens, str) or lens not in declared:
                    issues.append(f'{name}: references undeclared lens "{_show(lens)}"')
    return issues


def _build(data: dict[str, Any]) -> Rubric:
    return Rubric(
        version=data["version"],
        lenses=tuple(data["lenses"]),
        error_classes=tuple(
            ErrorClassDef(
                code=entry["code"],
                label=entry["label"],
                lenses=tuple(entry["lenses"]),
                include=entry["include"],
                exclude=entry["exclude"],
                good_example=entry.get("goodExample"),
                bad_example=entry.get("badExample"),
            )
            for entry in data["errorClasses"]
        ),
    )


def _as_json(
    version: object,
    lenses: object,
    error_classes: object,
) -> dict[str, Any]:
    classes: object = error_classes
    if isinstance(error_classes, (list, tuple)):
        classes = [
            entry.to_dict() if isinstance(entry, ErrorClassDef) else _camel_entry(entry)
            for entry in error_classes
        ]
    return {
        "version": version,
        "lenses": list(lenses) if isinstance(lenses, (list, tuple)) else lenses,
        "errorClasses": classes,
    }


def _camel_entry(entry: object) -> object:
    if not is_record(entry):
        return entry
    out = dict(entry)
    for snake, camel_key in (("good_example", "goodExample"), ("bad_example", "badExample")):
        if snake in out:
            out[camel_key] = out.pop(snake)
        if camel_key in out and out[camel_key] is None:
            del out[camel_key]
    if isinstance(out.get("lenses"), tuple):
        out["lenses"] = list(out["lenses"])
    return out


def define_rubric(
    *,
    version: str,
    lenses: Sequence[str],
    error_classes: Sequence[ErrorClassDef | dict[str, Any]],
) -> Rubric:
    """Validates a rubric written in code and returns an immutable copy.

    Checks: non-empty version; non-empty, unique lenses; unique, non-empty error-class codes;
    non-empty label/include/exclude; every error class references declared lenses only.
    Error classes may be :class:`ErrorClassDef` values or dicts (snake_case or camelCase).

    :raises RubricError: listing every issue when the rubric is invalid.
    """
    data = _as_json(version, lenses, error_classes)
    issues = _collect_issues(data)
    if issues:
        raise RubricError(issues)
    return _build(data)


def parse_rubric(data: object) -> Result[Rubric, list[str]]:
    """Validates untrusted input (for example a parsed ``rubric.json``).

    Returns a :data:`~evalroom.result.Result` instead of raising.
    """
    issues = _collect_issues(data)
    if issues or not is_record(data):
        return err(issues)
    return ok(_build(data))


def error_classes_for_lens(rubric: Rubric, lens: str) -> list[ErrorClassDef]:
    """Error classes the given lens may pick, in rubric order."""
    return [entry for entry in rubric.error_classes if lens in entry.lenses]


def find_error_class(rubric: Rubric, code: str) -> ErrorClassDef | None:
    """The error class with this code, or ``None``."""
    return next((entry for entry in rubric.error_classes if entry.code == code), None)
