from __future__ import annotations

import json

import pytest

from evalroom.evalrun import CaseResult, EvalRun, compare_runs, gate, score_run, summarize
from evalroom.golden import GoldenCase, GoldenProvenance
from evalroom.rules import PathRule, RuleFailure, custom


def results(passed: int, total: int) -> list[CaseResult]:
    return [
        CaseResult(
            case_id=f"c{i}",
            passed=i < passed,
            failures=[] if i < passed else [RuleFailure("X", "", "x")],
            output=None,
        )
        for i in range(total)
    ]


def run(run_id: str, outcomes: dict[str, bool], rubric_version: str = "v1") -> EvalRun:
    return EvalRun(
        id=run_id,
        label=run_id,
        rubric_version=rubric_version,
        results=[
            CaseResult(
                case_id=case_id,
                passed=passed,
                failures=[] if passed else [RuleFailure("F", "", "f")],
                output={"caseId": case_id},
            )
            for case_id, passed in outcomes.items()
        ],
    )


def golden(case_id: str) -> GoldenCase:
    return GoldenCase(
        id=case_id,
        item_id=case_id,
        input=None,
        reference_output=None,
        expected_rules=[PathRule("reply", "notContains", "guarantee", code="PROMISE")],
        provenance=GoldenProvenance([], "m", "2026-10-20T00:00:00.000Z"),
        rubric_version="v1",
    )


def test_score_run_fails_missing_outputs_and_applies_extra_rules() -> None:
    evaluated = score_run(
        [golden("a"), golden("b"), golden("c")],
        {"a": {"reply": "Your refund is on its way."}, "b": {"reply": "We guarantee a refund!"}},
        id="r1",
        label="baseline",
        created_at="2026-10-21T00:00:00.000Z",
        extra_rules=[custom("SHORT", lambda o: len(json.dumps(o)) < 200)],
    )
    assert evaluated.rubric_version == "v1"
    assert [(r.case_id, r.passed, [f.code for f in r.failures]) for r in evaluated.results] == [
        ("a", True, []),
        ("b", False, ["PROMISE"]),
        ("c", False, ["MISSING_OUTPUT"]),
    ]
    per_case = score_run(
        [golden("a")],
        {"a": {"reply": "ok"}},
        id="r2",
        label="x",
        extra_rules=lambda g: [custom("NAMED", lambda o: g.id == "a")],
    )
    assert per_case.results[0].passed is True
    assert per_case.created_at is not None


def test_summarize_reports_an_interval_and_failure_counts() -> None:
    summary = summarize(results(12, 20))
    assert (summary.total, summary.passed, summary.failed) == (20, 12, 8)
    assert summary.failures_by_code == {"X": 8}
    assert (summary.pass_rate.k, summary.pass_rate.n, summary.pass_rate.decidable) == (
        12,
        20,
        False,
    )
    assert summary.pass_rate.low == pytest.approx(0.3866, abs=5e-5)


def test_compare_partitions_shared_cases_in_base_order() -> None:
    comparison = compare_runs(
        run("base", {"a": True, "b": False, "c": True, "d": False, "e": True}),
        run("candidate", {"e": True, "d": False, "c": False, "b": True, "a": True, "z": True}),
    )
    assert [row.case_id for row in comparison.broken] == ["c"]
    assert [row.case_id for row in comparison.fixed] == ["b"]
    assert [row.case_id for row in comparison.same] == ["a", "d", "e"]
    assert comparison.only_in_candidate == ["z"]
    assert comparison.only_in_base == []
    assert comparison.broken[0].candidate.failures[0].code == "F"
    assert (comparison.base_summary.pass_rate.k, comparison.base_summary.pass_rate.n) == (3, 5)
    assert comparison.rubric_version_mismatch is False


def test_compare_one_sided_cases_and_rubric_mismatch() -> None:
    comparison = compare_runs(
        run("base", {"a": False, "b": True}, "v1"), run("cand", {"a": True}, "v2")
    )
    assert [row.case_id for row in comparison.fixed] == ["a"]
    assert comparison.broken == []
    assert comparison.only_in_base == ["b"]
    assert comparison.rubric_version_mismatch is True


def test_gate_undecidable_on_zero_results() -> None:
    assert gate([], min_pass_rate=0.9).decision == "undecidable"


def test_gate_undecidable_below_min_n_even_when_everything_passes() -> None:
    result = gate(results(11, 11), min_pass_rate=0.5)
    assert result.decision == "undecidable"
    assert result.reason.startswith("n=11 is below minN=50")
    assert (result.interval.k, result.interval.n) == (11, 11)


def test_gate_undecidable_when_too_wide() -> None:
    # 25/50: Wilson 0.366–0.634, width 0.267 > 0.25.
    result = gate(results(25, 50), min_pass_rate=0.4)
    assert result.decision == "undecidable"
    assert "exceeds maxWidth 0.25" in result.reason
    assert result.reason.startswith("interval width 0.267")


def test_gate_passes_only_when_the_lower_bound_clears_the_bar() -> None:
    # 97/100: Wilson lower bound 0.9154.
    result = gate(results(97, 100), min_pass_rate=0.9)
    assert result.decision == "pass"
    assert result.interval.low == pytest.approx(0.9154, abs=5e-4)
    assert result.reason == "lower bound 92% clears the 90% bar (97% (97/100) · 92%–99%)"


def test_gate_fails_when_the_point_estimate_is_below_the_bar() -> None:
    assert gate(results(80, 100), min_pass_rate=0.9).decision == "fail"


def test_gate_straddling_the_bar_is_undecidable() -> None:
    # 92/100: p = 0.92 >= 0.9, lower bound 0.850 < 0.9.
    result = gate(results(92, 100), min_pass_rate=0.9)
    assert result.decision == "undecidable"
    assert "collect more cases" in result.reason


def test_gate_custom_options_and_eval_run() -> None:
    evaluated = EvalRun(id="r", label="r", rubric_version="v1", results=results(20, 20))
    assert gate(evaluated, min_pass_rate=0.8, min_n=20, max_width=0.3).decision == "pass"


def test_gate_rejects_bar_outside_unit_interval() -> None:
    with pytest.raises(ValueError, match="must be in"):
        gate(results(1, 1), min_pass_rate=90)
