"""Eval runs.

Score outputs against golden cases, summarise them with intervals, compare two runs case by
case, and gate a run on its pass rate's confidence interval.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ._js import number_to_string, to_fixed
from ._serde import optional, to_json
from ._time import now_iso
from .golden import GoldenCase
from .rules import Rule, RuleFailure, score_output
from .stats import (
    DEFAULT_MAX_WIDTH,
    DEFAULT_MIN_N,
    DEFAULT_Z,
    Interval,
    format_interval,
    percent,
    wilson,
)

__all__ = [
    "MISSING_OUTPUT",
    "CaseResult",
    "ComparedCase",
    "EvalRun",
    "GateDecision",
    "GateResult",
    "RunComparison",
    "RunSummary",
    "compare_runs",
    "decide",
    "gate",
    "score_run",
    "summarize",
]

MISSING_OUTPUT = "MISSING_OUTPUT"
"""Failure code for a golden case that has no output in the run."""


@dataclass(frozen=True, slots=True)
class CaseResult:
    """The result of scoring one output against one golden case."""

    case_id: str
    """:attr:`GoldenCase.id`."""
    passed: bool
    """``True`` when no rule failed."""
    failures: list[RuleFailure]
    """Every failed rule."""
    output: Any
    """The output that was scored."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of one run-file line."""
        result: dict[str, Any] = to_json(self)
        return result


@dataclass(frozen=True, slots=True)
class EvalRun:
    """A scored run over a golden set."""

    id: str
    """Run id."""
    label: str
    """Human label, e.g. ``"baseline"``, ``"prompt-v7"``."""
    rubric_version: str
    """Rubric version of the golden cases scored."""
    results: list[CaseResult]
    """One result per case."""
    created_at: str | None = optional()
    """ISO-8601 timestamp."""
    tenant: str | None = optional()
    """Optional tenant tag."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape (the run header fields plus ``results``)."""
        result: dict[str, Any] = to_json(self)
        return result


Results = EvalRun | Sequence[CaseResult]


def score_run(
    cases: Sequence[GoldenCase],
    outputs: Mapping[str, Any],
    *,
    id: str,
    label: str,
    created_at: str | None = None,
    rubric_version: str | None = None,
    extra_rules: Sequence[Rule] | Callable[[GoldenCase], Sequence[Rule]] | None = None,
    tenant: str | None = None,
) -> EvalRun:
    """Scores one output per golden case.

    ``outputs`` is keyed by case id. A case with no output fails with code
    :data:`MISSING_OUTPUT` rather than being skipped, so a run cannot look better by dropping
    cases. ``extra_rules`` (possibly custom) are applied to every case after its expected rules.
    """
    results: list[CaseResult] = []
    for golden in cases:
        if golden.id not in outputs:
            results.append(
                CaseResult(
                    case_id=golden.id,
                    passed=False,
                    failures=[
                        RuleFailure(
                            code=MISSING_OUTPUT,
                            path="",
                            detail="no output was produced for this case",
                        )
                    ],
                    output=None,
                )
            )
            continue
        output = outputs[golden.id]
        extra = extra_rules(golden) if callable(extra_rules) else (extra_rules or [])
        score = score_output(output, [*golden.expected_rules, *extra])
        results.append(
            CaseResult(
                case_id=golden.id, passed=score.passed, failures=score.failures, output=output
            )
        )
    versions = sorted({golden.rubric_version for golden in cases})
    return EvalRun(
        id=id,
        label=label,
        rubric_version=rubric_version if rubric_version is not None else ",".join(versions),
        created_at=created_at if created_at is not None else now_iso(),
        results=results,
        tenant=tenant,
    )


def _results_of(run: Results) -> Sequence[CaseResult]:
    return run.results if isinstance(run, EvalRun) else run


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Output of :func:`summarize`."""

    total: int
    """Cases scored."""
    passed: int
    """Cases passed."""
    failed: int
    """Cases failed."""
    pass_rate: Interval
    """Pass rate with its Wilson interval."""
    failures_by_code: dict[str, int]
    """How many failures each code produced (one case can fail several codes)."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape."""
        result: dict[str, Any] = to_json(self)
        return result


def summarize(
    run: Results,
    *,
    z: float = DEFAULT_Z,
    min_n: float = DEFAULT_MIN_N,
    max_width: float = DEFAULT_MAX_WIDTH,
) -> RunSummary:
    """Pass rate (as an interval) and failure counts for a run."""
    results = _results_of(run)
    passed = sum(1 for result in results if result.passed)
    by_code: dict[str, int] = {}
    for result in results:
        for failure in result.failures:
            by_code[failure.code] = by_code.get(failure.code, 0) + 1
    return RunSummary(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        pass_rate=wilson(passed, len(results), z=z, min_n=min_n, max_width=max_width),
        failures_by_code=by_code,
    )


@dataclass(frozen=True, slots=True)
class ComparedCase:
    """One case present in both runs."""

    case_id: str
    base: CaseResult
    candidate: CaseResult


@dataclass(frozen=True, slots=True)
class RunComparison:
    """Output of :func:`compare_runs`."""

    broken: list[ComparedCase]
    """Passed in base, failed in candidate: regressions."""
    fixed: list[ComparedCase]
    """Failed in base, passed in candidate."""
    same: list[ComparedCase]
    """Same outcome in both (both passed or both failed)."""
    only_in_base: list[str]
    """Case ids only the base run scored."""
    only_in_candidate: list[str]
    """Case ids only the candidate run scored."""
    base_summary: RunSummary
    """Summary of the base run."""
    candidate_summary: RunSummary
    """Summary of the candidate run."""
    rubric_version_mismatch: bool
    """``True`` when both runs are :class:`EvalRun` values with different rubric versions."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape."""
        result: dict[str, Any] = to_json(self)
        return result


def compare_runs(
    base: Results,
    candidate: Results,
    *,
    z: float = DEFAULT_Z,
    min_n: float = DEFAULT_MIN_N,
    max_width: float = DEFAULT_MAX_WIDTH,
) -> RunComparison:
    """Partitions the cases both runs scored into broken, fixed and same, in base-run order.

    Cases present in only one run are listed separately and never counted as fixed or broken.
    """
    base_results = _results_of(base)
    candidate_results = _results_of(candidate)
    candidate_by_id: dict[str, CaseResult] = {}
    for result in candidate_results:
        candidate_by_id[result.case_id] = result
    base_ids = {result.case_id for result in base_results}
    broken: list[ComparedCase] = []
    fixed: list[ComparedCase] = []
    same: list[ComparedCase] = []
    only_in_base: list[str] = []
    for result in base_results:
        nxt = candidate_by_id.get(result.case_id)
        if nxt is None:
            only_in_base.append(result.case_id)
            continue
        row = ComparedCase(case_id=result.case_id, base=result, candidate=nxt)
        if result.passed and not nxt.passed:
            broken.append(row)
        elif not result.passed and nxt.passed:
            fixed.append(row)
        else:
            same.append(row)
    base_version = base.rubric_version if isinstance(base, EvalRun) else None
    candidate_version = candidate.rubric_version if isinstance(candidate, EvalRun) else None
    options = {"z": z, "min_n": min_n, "max_width": max_width}
    return RunComparison(
        broken=broken,
        fixed=fixed,
        same=same,
        only_in_base=only_in_base,
        only_in_candidate=[r.case_id for r in candidate_results if r.case_id not in base_ids],
        base_summary=summarize(base_results, **options),
        candidate_summary=summarize(candidate_results, **options),
        rubric_version_mismatch=(
            base_version is not None
            and candidate_version is not None
            and base_version != candidate_version
        ),
    )


GateDecision = Literal["pass", "fail", "undecidable"]
"""``pass``, ``fail``, or ``undecidable`` when the data cannot support either."""


@dataclass(frozen=True, slots=True)
class GateResult:
    """Output of :func:`gate`."""

    decision: GateDecision
    """The decision."""
    interval: Interval
    """The pass rate's Wilson interval the decision rests on."""
    min_pass_rate: float
    """The bar that was applied."""
    reason: str
    """One sentence explaining the decision."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape: ``{"decision", "interval", "minPassRate", "reason"}``."""
        result: dict[str, Any] = to_json(self)
        return result


def gate(
    results: Results,
    *,
    min_pass_rate: float,
    min_n: float = DEFAULT_MIN_N,
    max_width: float = DEFAULT_MAX_WIDTH,
    z: float = DEFAULT_Z,
) -> GateResult:
    """Gates a run on the Wilson interval of its pass rate.

    - ``undecidable`` when ``n < min_n`` or the interval is wider than ``max_width``: no
      decision is taken on too little data, in either direction;
    - ``pass`` when the interval's lower bound is at or above ``min_pass_rate``;
    - ``fail`` when the point estimate is below ``min_pass_rate``;
    - ``undecidable`` otherwise: the point estimate clears the bar but the lower bound does not.

    :raises ValueError: when ``min_pass_rate`` is not in [0, 1].
    """
    if (
        not isinstance(min_pass_rate, (int, float))
        or not math.isfinite(min_pass_rate)
        or min_pass_rate < 0
        or min_pass_rate > 1
    ):
        raise ValueError(
            f"gate: min_pass_rate must be in [0, 1], got {number_to_string(min_pass_rate)}"
        )
    items = _results_of(results)
    passed = sum(1 for result in items if result.passed)
    interval = wilson(passed, len(items), z=z, min_n=min_n, max_width=max_width)
    return decide(interval, min_pass_rate, min_n=min_n, max_width=max_width)


def decide(
    interval: Interval,
    bar: float,
    *,
    min_n: float = DEFAULT_MIN_N,
    max_width: float = DEFAULT_MAX_WIDTH,
    noun: str = "pass rate",
    unit: str = "cases",
) -> GateResult:
    """The decision rule behind :func:`gate`, applied to any interval and bar.

    ``noun`` and ``unit`` only change the wording of the reason (``judge-check`` uses
    "agreement" and "labels").
    """
    shown = format_interval(interval)
    bar_text = percent(bar)

    def verdict(decision: GateDecision, reason: str) -> GateResult:
        return GateResult(decision=decision, interval=interval, min_pass_rate=bar, reason=reason)

    if interval.n == 0:
        return verdict("undecidable", f"no results; a gate never decides on zero {unit}")
    if interval.n < min_n:
        return verdict(
            "undecidable",
            f"n={number_to_string(interval.n)} is below minN={number_to_string(min_n)} ({shown})",
        )
    width = interval.high - interval.low
    if width > max_width:
        return verdict(
            "undecidable",
            f"interval width {to_fixed(width, 3)} exceeds maxWidth "
            f"{number_to_string(max_width)} ({shown})",
        )
    if interval.low >= bar:
        return verdict(
            "pass", f"lower bound {percent(interval.low)} clears the {bar_text} bar ({shown})"
        )
    if interval.p < bar:
        return verdict(
            "fail", f"{noun} {percent(interval.p)} is below the {bar_text} bar ({shown})"
        )
    return verdict(
        "undecidable",
        f"{noun} {percent(interval.p)} meets the {bar_text} bar but the lower bound "
        f"{percent(interval.low)} does not; collect more {unit} ({shown})",
    )
