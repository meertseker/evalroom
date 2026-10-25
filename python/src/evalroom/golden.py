"""Golden cases.

A reviewed item becomes a golden case only when every lens has a current verdict and (by
default) every one of them is PASS. The case freezes the input, the reviewed output and the
rules a future output must satisfy, with provenance back to the reviews.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ._serde import MISSING, optional, to_json
from ._time import now_iso
from .audit import AuditEvent, AuditSink
from .result import Err, Ok, Result, err, is_record, ok
from .review import Item, Review, latest_per_lens, missing_lenses
from .rubric import Rubric
from .rules import PathRule, parse_rules, score_output

__all__ = [
    "GoldenCase",
    "GoldenProvenance",
    "PromotionRefusal",
    "PromotionRefusalCode",
    "golden_from_dict",
    "promote_and_record",
    "promote_to_golden",
    "promotion_audit_event",
]


@dataclass(frozen=True, slots=True)
class GoldenProvenance:
    """Where a golden case came from."""

    review_ids: list[str]
    """Ids of the current lens reviews the promotion was based on, in rubric lens order."""
    promoted_by: str
    """Who promoted it."""
    promoted_at: str
    """ISO-8601 timestamp of the promotion."""


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """A frozen, reviewed case that future outputs are scored against."""

    id: str
    """Case id. Default ``golden-<item_id>``."""
    item_id: str
    """The reviewed :attr:`Item.id`."""
    input: Any
    """The item's input, frozen. ``None`` when the item had none."""
    reference_output: Any
    """The output the reviewers graded, frozen."""
    expected_rules: list[PathRule]
    """Rules a new output for this input must satisfy. JSON-serialisable."""
    provenance: GoldenProvenance
    """Provenance back to the reviews."""
    rubric_version: str
    """Rubric version the reviews were written against."""
    title: str | None = optional()
    """Optional human title."""
    tenant: str | None = optional()
    """Optional tenant tag, copied from the item."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of one ``golden.jsonl`` line."""
        result: dict[str, Any] = to_json(self)
        return result


def golden_from_dict(data: object) -> GoldenCase:
    """Reads one ``golden.jsonl`` line.

    :raises ValueError: when ``id``/``itemId`` are missing or the rules are invalid.
    """
    if not is_record(data) or not isinstance(data.get("id"), str):
        raise ValueError("a golden case needs string id and itemId")
    if not isinstance(data.get("itemId"), str):
        raise ValueError("a golden case needs string id and itemId")
    rules = parse_rules(data.get("expectedRules"))
    if isinstance(rules, Err):
        raise ValueError("; ".join(rules.error))
    provenance = data.get("provenance")
    prov = provenance if is_record(provenance) else {}
    review_ids = prov.get("reviewIds")
    return GoldenCase(
        id=data["id"],
        item_id=data["itemId"],
        input=data.get("input"),
        reference_output=data.get("referenceOutput"),
        expected_rules=rules.value,
        provenance=GoldenProvenance(
            review_ids=list(review_ids) if isinstance(review_ids, list) else [],
            promoted_by=str(prov.get("promotedBy", "")),
            promoted_at=str(prov.get("promotedAt", "")),
        ),
        rubric_version=str(data.get("rubricVersion", "")),
        title=data.get("title") if isinstance(data.get("title"), str) else None,
        tenant=data.get("tenant") if isinstance(data.get("tenant"), str) else None,
    )


PromotionRefusalCode = Literal[
    "MISSING_LENSES",
    "NOT_UNANIMOUS_PASS",
    "NO_RULES",
    "INVALID_RULES",
    "NO_PROMOTER",
    "REFERENCE_OUTPUT_FAILS",
]
"""Why a promotion was refused."""


@dataclass(frozen=True, slots=True)
class PromotionRefusal:
    """One reason a promotion was refused."""

    code: PromotionRefusalCode
    """Machine code."""
    message: str
    """Human-readable explanation."""
    lenses: list[str] | None = optional()
    """Lenses involved, for ``MISSING_LENSES`` and ``NOT_UNANIMOUS_PASS``."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape."""
        result: dict[str, Any] = to_json(self)
        return result


def promote_to_golden(
    item: Item,
    reviews: Sequence[Review],
    *,
    rubric: Rubric,
    expected_rules: Sequence[PathRule | dict[str, Any]],
    promoted_by: str,
    id: str | None = None,
    promoted_at: str | None = None,
    title: str | None = None,
    require_unanimous_pass: bool = True,
    check_reference_output: bool = True,
) -> Result[GoldenCase, list[PromotionRefusal]]:
    """Promotes a reviewed item to a golden case, or refuses with every reason at once.

    Preconditions, all checked:

    1. every rubric lens has a current review of ``rubric.version`` for this item;
    2. (default) every current verdict is PASS;
    3. ``expected_rules`` is non-empty and valid;
    4. ``promoted_by`` is non-empty;
    5. (default) the rules pass on the item's reviewed output.

    Pure: writes nothing. Use :func:`promotion_audit_event` or :func:`promote_and_record`.
    """
    refusals: list[PromotionRefusal] = []
    relevant = [
        review
        for review in reviews
        if review.item_id == item.id and review.rubric_version == rubric.version
    ]
    missing = missing_lenses(relevant, rubric.lenses)
    if missing:
        plural = "es" if len(missing) > 1 else ""
        refusals.append(
            PromotionRefusal(
                code="MISSING_LENSES",
                message=(
                    f"no current {rubric.version} verdict for lens{plural}: {', '.join(missing)}"
                ),
                lenses=missing,
            )
        )
    current = latest_per_lens(relevant, rubric.lenses)
    if require_unanimous_pass:
        not_pass = [review for review in current if review.verdict != "PASS"]
        if not_pass:
            listed = ", ".join(f"{review.lens}={review.verdict}" for review in not_pass)
            refusals.append(
                PromotionRefusal(
                    code="NOT_UNANIMOUS_PASS",
                    message=f"not every lens passed: {listed}",
                    lenses=[review.lens for review in not_pass],
                )
            )
    rules: list[PathRule] = []
    if len(expected_rules) == 0:
        refusals.append(
            PromotionRefusal(
                code="NO_RULES", message="a golden case needs at least one expected rule"
            )
        )
    else:
        parsed = parse_rules(list(expected_rules))
        if isinstance(parsed, Ok):
            rules = parsed.value
        else:
            refusals.append(PromotionRefusal(code="INVALID_RULES", message="; ".join(parsed.error)))
    if promoted_by.strip() == "":
        refusals.append(
            PromotionRefusal(code="NO_PROMOTER", message="promotedBy must name who is promoting")
        )
    if rules and check_reference_output:
        score = score_output(item.output, rules)
        if not score.passed:
            listed = "; ".join(f"{f.code} ({f.detail})" for f in score.failures)
            refusals.append(
                PromotionRefusal(
                    code="REFERENCE_OUTPUT_FAILS",
                    message=f"the expected rules fail on the reviewed output: {listed}",
                )
            )
    if refusals:
        return err(refusals)
    return ok(
        GoldenCase(
            id=id if id is not None else f"golden-{item.id}",
            item_id=item.id,
            input=None if item.input is MISSING else copy.deepcopy(item.input),
            reference_output=copy.deepcopy(item.output),
            expected_rules=copy.deepcopy(rules),
            provenance=GoldenProvenance(
                review_ids=[review.id for review in current],
                promoted_by=promoted_by,
                promoted_at=promoted_at if promoted_at is not None else now_iso(),
            ),
            rubric_version=rubric.version,
            title=title,
            tenant=item.tenant,
        )
    )


def promotion_audit_event(golden: GoldenCase) -> AuditEvent:
    """The audit event recording a promotion."""
    return AuditEvent(
        at=golden.provenance.promoted_at,
        actor=golden.provenance.promoted_by,
        action="golden.promote",
        target=golden.id,
        after={
            "itemId": golden.item_id,
            "rubricVersion": golden.rubric_version,
            "reviewIds": list(golden.provenance.review_ids),
            "rules": len(golden.expected_rules),
        },
        tenant=golden.tenant,
    )


def promote_and_record(
    item: Item,
    reviews: Sequence[Review],
    audit: AuditSink,
    **options: Any,
) -> Result[GoldenCase, list[PromotionRefusal]]:
    """:func:`promote_to_golden`, then on success appends the audit event to ``audit``.

    Refusals are not audited. ``options`` are the keyword arguments of
    :func:`promote_to_golden`.
    """
    result = promote_to_golden(item, reviews, **options)
    if isinstance(result, Ok):
        audit.append(promotion_audit_event(result.value))
    return result
