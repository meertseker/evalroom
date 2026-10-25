"""Inter-lens agreement.

Answers "can we trust our graders?" for one rubric version: raw agreement, Fleiss' kappa
across lenses, Cohen's kappa per lens pair, prevalence, pairwise confusion, review time and
the list of disagreements.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ._js import math_round, to_fixed
from ._serde import optional, to_json
from .review import Review, complete_items, incomplete_items
from .rubric import VERDICTS, Rubric, Severity, Verdict
from .stats import (
    DEFAULT_MAX_WIDTH,
    DEFAULT_MIN_N,
    DEFAULT_Z,
    Interval,
    KappaBand,
    cohen_kappa,
    fleiss_kappa,
    interpret_kappa,
    median,
    wilson,
)

__all__ = [
    "DEFAULT_MIN_KAPPA_ITEMS",
    "AgreementReport",
    "Disagreement",
    "DisagreementVerdict",
    "KappaStat",
    "LensPairStat",
    "agreement_report",
    "kappa_stat",
]

DEFAULT_MIN_KAPPA_ITEMS = 10
"""Below this many complete items kappa is reported as ``None`` with a reason."""


@dataclass(frozen=True, slots=True)
class KappaStat:
    """A kappa value with its sample size, its Landis & Koch band and, when ``None``, why."""

    value: float | None
    """Kappa, or ``None`` when it was not computed or is undefined."""
    band: KappaBand | None
    """Landis & Koch band of ``value``."""
    n: int
    """Number of items the value is computed over."""
    reason: str | None = optional()
    """Present when ``value`` is ``None``."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape."""
        result: dict[str, Any] = to_json(self)
        return result


@dataclass(frozen=True, slots=True)
class LensPairStat:
    """Agreement between two lenses over the complete items."""

    a: str
    """First lens (rows of ``confusion``)."""
    b: str
    """Second lens (columns of ``confusion``)."""
    agreement: Interval
    """Share of items where both lenses gave the same verdict."""
    cohen_kappa: KappaStat
    """Cohen's kappa between the two lenses."""
    confusion: dict[str, dict[str, int]]
    """``confusion[verdict_of_a][verdict_of_b]`` = number of items."""


@dataclass(frozen=True, slots=True)
class DisagreementVerdict:
    """One lens verdict inside a :class:`Disagreement`."""

    lens: str
    verdict: Verdict
    reviewer: str
    review_id: str
    error_class: str | None = optional()
    severity: Severity | None = optional()
    note: str | None = optional()

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape, in the key order the TypeScript implementation writes."""
        out: dict[str, Any] = {"lens": self.lens, "verdict": self.verdict}
        out["reviewer"] = self.reviewer
        out["reviewId"] = self.review_id
        if self.error_class is not None:
            out["errorClass"] = self.error_class
        if self.severity is not None:
            out["severity"] = self.severity
        if self.note is not None:
            out["note"] = self.note
        return out


@dataclass(frozen=True, slots=True)
class Disagreement:
    """A complete item whose current lens verdicts are not all the same."""

    item_id: str
    verdicts: list[DisagreementVerdict]


@dataclass(frozen=True, slots=True)
class AgreementReport:
    """Output of :func:`agreement_report`."""

    rubric_version: str
    """The rubric version the report is restricted to."""
    lenses: list[str]
    """Lenses, in rubric order."""
    items: int
    """Complete items (every lens has a current verdict). Only these enter the statistics."""
    incomplete_items: int
    """Items with some but not all lens verdicts, in this rubric version."""
    excluded_reviews: int
    """Reviews skipped because they were written against another rubric version."""
    raw_agreement: Interval
    """Share of complete items where every lens agrees."""
    fleiss_kappa: KappaStat
    """Fleiss' kappa with lenses as raters."""
    pairs: list[LensPairStat]
    """Per lens pair: agreement, Cohen's kappa and a confusion matrix."""
    prevalence: dict[str, Interval]
    """Share of current verdicts that are PASS, FAIL and NEEDS_REVIEW (n = items x lenses)."""
    median_seconds: float | None
    """Median ``seconds`` over all reviews in this version that recorded a time."""
    median_seconds_by_lens: dict[str, float | None]
    """Median ``seconds`` per lens."""
    disagreements: list[Disagreement]
    """Complete items where the lenses split, in input order."""
    interpretation: str
    """One-sentence reading of the kappa using Landis & Koch bands, with caveats."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape printed by ``evalroom agreement --json``."""
        out: dict[str, Any] = to_json(self)
        out["disagreements"] = [
            {"itemId": d.item_id, "verdicts": [v.to_dict() for v in d.verdicts]}
            for d in self.disagreements
        ]
        return out


def _empty_confusion() -> dict[str, dict[str, int]]:
    return {row: dict.fromkeys(VERDICTS, 0) for row in VERDICTS}


def kappa_stat(value: float | None, n: int, min_items: int, undefined_reason: str) -> KappaStat:
    """Wraps a kappa: ``None`` with a reason below ``min_items`` or when undefined."""
    if n < min_items:
        plural = "" if n == 1 else "s"
        return KappaStat(
            value=None,
            band=None,
            n=n,
            reason=f"{n} complete item{plural}; kappa needs at least {min_items}",
        )
    if value is None:
        return KappaStat(value=None, band=None, n=n, reason=undefined_reason)
    return KappaStat(value=value, band=interpret_kappa(value), n=n)


def agreement_report(
    reviews: Sequence[Review],
    rubric: Rubric,
    *,
    rubric_version: str | None = None,
    min_items: int = DEFAULT_MIN_KAPPA_ITEMS,
    z: float = DEFAULT_Z,
    min_n: float = DEFAULT_MIN_N,
    max_width: float = DEFAULT_MAX_WIDTH,
) -> AgreementReport:
    """Builds the agreement report for one rubric version.

    Only reviews stamped with that version count; only items where every lens has a current
    (latest) verdict enter the statistics. Kappa is ``None``, with a reason, below
    ``min_items`` complete items or when it is undefined (no variation in verdicts).

    Fleiss' kappa treats the lenses as raters of the same item. That measures whether the
    lenses converge on one verdict; see docs/statistics.md for what that does and does not
    mean. ``z``, ``min_n`` and ``max_width`` are forwarded to every Wilson interval.
    """
    version = rubric_version if rubric_version is not None else rubric.version
    lenses = list(rubric.lenses)
    scoped = [review for review in reviews if review.rubric_version == version]
    items = complete_items(scoped, lenses)
    n = len(items)

    def interval(k: float, total: float) -> Interval:
        return wilson(k, total, z=z, min_n=min_n, max_width=max_width)

    counts: list[dict[str, int]] = []
    for item in items:
        row: dict[str, int] = {}
        for review in item.reviews:
            row[review.verdict] = row.get(review.verdict, 0) + 1
        counts.append(row)
    unanimous = sum(1 for row in counts if len(row) == 1)

    total_verdicts = n * len(lenses)
    prevalence: dict[str, Interval] = {
        verdict: interval(sum(row.get(verdict, 0) for row in counts), total_verdicts)
        for verdict in VERDICTS
    }

    pairs: list[LensPairStat] = []
    for i in range(len(lenses)):
        for j in range(i + 1, len(lenses)):
            confusion = _empty_confusion()
            labels_a: list[str] = []
            labels_b: list[str] = []
            agreed = 0
            for item in items:
                va = item.reviews[i].verdict
                vb = item.reviews[j].verdict
                confusion[va][vb] += 1
                labels_a.append(va)
                labels_b.append(vb)
                if va == vb:
                    agreed += 1
            pairs.append(
                LensPairStat(
                    a=lenses[i],
                    b=lenses[j],
                    agreement=interval(agreed, n),
                    cohen_kappa=kappa_stat(
                        cohen_kappa(labels_a, labels_b) if n >= min_items else None,
                        n,
                        min_items,
                        "both lenses used a single identical verdict; kappa is undefined",
                    ),
                    confusion=confusion,
                )
            )

    if len(lenses) < 2:
        fleiss = KappaStat(value=None, band=None, n=n, reason="kappa needs at least two lenses")
    else:
        fleiss = kappa_stat(
            fleiss_kappa(counts) if n >= min_items else None,
            n,
            min_items,
            "every verdict is identical; kappa is undefined without variation",
        )

    timed = [r for r in scoped if r.seconds is not None and r.lens in lenses]
    by_lens = {
        lens: median([r.seconds for r in timed if r.lens == lens and r.seconds is not None])
        for lens in lenses
    }

    disagreements = [
        Disagreement(
            item_id=item.item_id,
            verdicts=[
                DisagreementVerdict(
                    lens=review.lens,
                    verdict=review.verdict,
                    reviewer=review.reviewer,
                    review_id=review.id,
                    error_class=review.error_class,
                    severity=review.severity,
                    note=review.note,
                )
                for review in item.reviews
            ],
        )
        for item in items
        if len({review.verdict for review in item.reviews}) > 1
    ]

    return AgreementReport(
        rubric_version=version,
        lenses=lenses,
        items=n,
        incomplete_items=len(incomplete_items(scoped, lenses)),
        excluded_reviews=len(reviews) - len(scoped),
        raw_agreement=interval(unanimous, n),
        fleiss_kappa=fleiss,
        pairs=pairs,
        prevalence=prevalence,
        median_seconds=median([r.seconds for r in timed if r.seconds is not None]),
        median_seconds_by_lens=by_lens,
        disagreements=disagreements,
        interpretation=_interpret(fleiss, len(lenses), prevalence),
    )


def _interpret(fleiss: KappaStat, lens_count: int, prevalence: dict[str, Interval]) -> str:
    if fleiss.value is None:
        return f"Kappa not reported: {fleiss.reason}."
    parts = [
        f"Fleiss' kappa {to_fixed(fleiss.value, 2)} across {lens_count} lenses on "
        f"{fleiss.n} complete items: {fleiss.band} agreement (Landis & Koch)."
    ]
    dominant = next((v for v in VERDICTS if prevalence[v].p >= 0.8), None)
    if dominant is not None:
        parts.append(
            f"{dominant} is {math_round(prevalence[dominant].p * 100)}% of verdicts; kappa is "
            "depressed when one verdict dominates, so read raw agreement alongside it."
        )
    if fleiss.n < 50:
        parts.append(
            f"With {fleiss.n} items the kappa itself is noisy; treat the band as provisional."
        )
    return " ".join(parts)
