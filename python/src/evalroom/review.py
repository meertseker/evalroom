"""Items and reviews.

A review is one reviewer's verdict on one item through one lens. Reviews are append-only: the
latest review per (item, lens) wins, earlier ones are kept as history.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from ._js import math_round
from ._serde import MISSING, optional, to_json
from ._time import parse_time
from .result import Result, err, is_record, ok
from .rubric import Rubric, Severity, Verdict, find_error_class, is_severity, is_verdict

__all__ = [
    "CompleteItem",
    "IncompleteItem",
    "Item",
    "Review",
    "complete_items",
    "group_by_item",
    "incomplete_items",
    "latest_per_lens",
    "missing_lenses",
    "normalize_seconds",
    "parse_item",
    "validate_review",
]


@dataclass(slots=True)
class Item:
    """One LLM output under review."""

    id: str
    """Stable id, unique within a dataset."""
    output: Any
    """The output being judged. Any JSON-serialisable value."""
    input: Any = field(default=MISSING)
    """The input that produced the output; :data:`~evalroom._serde.MISSING` when absent."""
    meta: dict[str, Any] | None = optional()
    """Free-form metadata (model, prompt version, trace id, ...)."""
    tenant: str | None = optional()
    """Optional tenant tag. evalroom never filters on it implicitly."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of one ``items.jsonl`` line."""
        result: dict[str, Any] = to_json(self)
        return result


@dataclass(slots=True)
class Review:
    """One verdict on one item through one lens."""

    id: str
    """Unique id of this review."""
    item_id: str
    """The :attr:`Item.id` under review."""
    lens: str
    """Lens this verdict is given through."""
    verdict: Verdict
    """``PASS``, ``FAIL`` or ``NEEDS_REVIEW``."""
    reviewer: str
    """Who gave the verdict."""
    rubric_version: str
    """:attr:`Rubric.version` the reviewer graded against."""
    created_at: str
    """ISO-8601 timestamp. Decides which review is the latest for its (item, lens)."""
    error_class: str | None = optional()
    """Error-class code from the rubric. Required on ``FAIL`` unless validation is relaxed."""
    severity: Severity | None = optional()
    """Severity of the failure. Required on ``FAIL`` unless validation is relaxed."""
    note: str | None = optional()
    """Free-text reasoning. Required on ``FAIL`` unless validation is relaxed."""
    seconds: float | None = optional()
    """Seconds the reviewer spent; feeds the median review time."""
    tenant: str | None = optional()
    """Optional tenant tag."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of one ``reviews.jsonl`` line (camelCase keys)."""
        result: dict[str, Any] = to_json(self)
        return result


def _str(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def parse_item(data: object) -> Result[Item, list[str]]:
    """Validates untrusted input (one ``items.jsonl`` line) as an :class:`Item`."""
    if not is_record(data):
        return err(["item must be an object"])
    issues: list[str] = []
    if not _str(data.get("id")):
        issues.append("id must be a non-empty string")
    if "output" not in data:
        issues.append("output is required")
    if "meta" in data and not is_record(data["meta"]):
        issues.append("meta must be an object")
    if "tenant" in data and not isinstance(data["tenant"], str):
        issues.append("tenant must be a string")
    if issues:
        return err(issues)
    return ok(
        Item(
            id=data["id"],
            output=data["output"],
            input=data.get("input", MISSING),
            meta=data.get("meta"),
            tenant=data.get("tenant"),
        )
    )


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_review(
    data: object,
    *,
    rubric: Rubric | None = None,
    require_fail_details: bool = True,
) -> Result[Review, list[str]]:
    """Validates untrusted input (one ``reviews.jsonl`` line) as a :class:`Review`.

    A ``FAIL`` must carry ``errorClass``, ``severity`` and ``note`` (disable with
    ``require_fail_details=False``). With a ``rubric``, reviews stamped with that rubric's
    version must use a declared lens and an error class allowed for that lens. Returns every
    issue at once.
    """
    if not is_record(data):
        return err(["review must be an object"])
    issues: list[str] = []
    for key in ("id", "itemId", "lens", "reviewer", "rubricVersion", "createdAt"):
        if not _str(data.get(key)):
            issues.append(f"{key} must be a non-empty string")
    created_at = data.get("createdAt")
    if _str(created_at) and parse_time(str(created_at)) is None:
        issues.append("createdAt must be an ISO-8601 timestamp")
    verdict = data.get("verdict")
    if not is_verdict(verdict):
        issues.append("verdict must be PASS, FAIL or NEEDS_REVIEW")
    if "severity" in data and not is_severity(data["severity"]):
        issues.append("severity must be low, medium or high")
    if "errorClass" in data and not _str(data["errorClass"]):
        issues.append("errorClass must be a non-empty string when present")
    if "note" in data and not isinstance(data["note"], str):
        issues.append("note must be a string")
    seconds = data.get("seconds", MISSING)
    if seconds is not MISSING and (
        not _number(seconds) or not math.isfinite(seconds) or seconds < 0
    ):
        issues.append("seconds must be a non-negative number")
    if "tenant" in data and not isinstance(data["tenant"], str):
        issues.append("tenant must be a string")
    if verdict == "FAIL" and require_fail_details:
        if not _str(data.get("errorClass")):
            issues.append("a FAIL requires errorClass")
        if not is_severity(data.get("severity")):
            issues.append("a FAIL requires severity")
        if not _str(data.get("note")):
            issues.append("a FAIL requires a note")
    lens = data.get("lens")
    if rubric is not None and data.get("rubricVersion") == rubric.version and _str(lens):
        if lens not in rubric.lenses:
            issues.append(f'lens "{lens}" is not declared in rubric {rubric.version}')
        error_class = data.get("errorClass")
        if _str(error_class):
            entry = find_error_class(rubric, str(error_class))
            if entry is None:
                issues.append(f'errorClass "{error_class}" is not in rubric {rubric.version}')
            elif lens not in entry.lenses:
                issues.append(f'errorClass "{error_class}" is not available to lens "{lens}"')
    if issues or not is_verdict(verdict):
        return err(issues)
    severity = data.get("severity")
    return ok(
        Review(
            id=data["id"],
            item_id=data["itemId"],
            lens=data["lens"],
            verdict=verdict,
            reviewer=data["reviewer"],
            rubric_version=data["rubricVersion"],
            created_at=data["createdAt"],
            error_class=data["errorClass"] if _str(data.get("errorClass")) else None,
            severity=severity if is_severity(severity) else None,
            note=data["note"] if isinstance(data.get("note"), str) else None,
            seconds=seconds if _number(seconds) else None,
            tenant=data["tenant"] if isinstance(data.get("tenant"), str) else None,
        )
    )


def normalize_seconds(value: object, *, min: float = 1, max: float = 1800) -> int | None:
    """Rounds a review duration and drops implausible values.

    Missing, NaN, below ``min`` or above ``max`` become ``None``; numeric strings are accepted.
    The default ``max`` is thirty minutes: a tab left open is not review time.
    """
    number: float
    if isinstance(value, str) and value.strip():
        try:
            number = float(value)
        except ValueError:
            return None
    elif _number(value):
        number = float(value)  # type: ignore[arg-type]
    else:
        return None
    if not math.isfinite(number):
        return None
    rounded = math_round(number)
    return rounded if min <= rounded <= max else None


class _HasItemId(Protocol):
    @property
    def item_id(self) -> str: ...


class _HasLens(Protocol):
    @property
    def lens(self) -> str: ...

    @property
    def created_at(self) -> str: ...


R = TypeVar("R", bound=_HasItemId)
L = TypeVar("L", bound=_HasLens)


def group_by_item(reviews: Sequence[R]) -> dict[str, list[R]]:
    """Groups reviews by item id, preserving first-appearance order of items and input order."""
    by_item: dict[str, list[R]] = {}
    for review in reviews:
        by_item.setdefault(review.item_id, []).append(review)
    return by_item


def latest_per_lens(reviews: Sequence[L], lenses: Sequence[str]) -> list[L]:
    """The current review per lens for one item.

    The review with the latest ``created_at`` wins; on equal timestamps the one that appears
    later in ``reviews`` wins (append order). Reviews for lenses not in ``lenses`` are
    ignored. Returned in ``lenses`` order, absent lenses omitted.
    """
    latest: dict[str, tuple[L, float, int]] = {}
    for index, review in enumerate(reviews):
        if review.lens not in lenses:
            continue
        parsed = parse_time(review.created_at)
        at = -math.inf if parsed is None else parsed
        current = latest.get(review.lens)
        if current is None or at > current[1] or (at == current[1] and index > current[2]):
            latest[review.lens] = (review, at, index)
    return [latest[lens][0] for lens in lenses if lens in latest]


def missing_lenses(reviews: Sequence[_HasLens], lenses: Sequence[str]) -> list[str]:
    """Lenses in ``lenses`` that have no review for this item, in ``lenses`` order."""
    present = {review.lens for review in latest_per_lens(reviews, lenses)}
    return [lens for lens in lenses if lens not in present]


@dataclass(frozen=True, slots=True)
class CompleteItem:
    """An item where every lens has a current verdict."""

    item_id: str
    """The item id."""
    reviews: list[Review]
    """The current review per lens, in rubric lens order."""


@dataclass(frozen=True, slots=True)
class IncompleteItem:
    """An item with at least one review but at least one lens still missing."""

    item_id: str
    """The item id."""
    present: list[str]
    """Lenses with a current verdict."""
    missing: list[str]
    """Lenses without one."""


def _coverage(
    reviews: Sequence[Review], lenses: Sequence[str], rubric_version: str | None
) -> tuple[list[CompleteItem], list[IncompleteItem]]:
    scoped = (
        list(reviews)
        if rubric_version is None
        else [review for review in reviews if review.rubric_version == rubric_version]
    )
    complete: list[CompleteItem] = []
    incomplete: list[IncompleteItem] = []
    for item_id, rows in group_by_item(scoped).items():
        current = latest_per_lens(rows, lenses)
        if len(current) == len(lenses):
            complete.append(CompleteItem(item_id=item_id, reviews=current))
        else:
            present = [review.lens for review in current]
            if not present:
                continue
            incomplete.append(
                IncompleteItem(
                    item_id=item_id,
                    present=present,
                    missing=[lens for lens in lenses if lens not in present],
                )
            )
    return complete, incomplete


def complete_items(
    reviews: Sequence[Review], lenses: Sequence[str], *, rubric_version: str | None = None
) -> list[CompleteItem]:
    """Items whose every lens has a current verdict, with that verdict per lens.

    Only complete items enter agreement statistics or golden promotion.
    """
    return _coverage(reviews, lenses, rubric_version)[0]


def incomplete_items(
    reviews: Sequence[Review], lenses: Sequence[str], *, rubric_version: str | None = None
) -> list[IncompleteItem]:
    """Items that have some lens verdicts but not all. Useful as a review queue."""
    return _coverage(reviews, lenses, rubric_version)[1]
