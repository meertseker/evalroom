"""Shared conformance vectors (``conformance/*.json``), generated from the TypeScript
implementation. Python must reproduce every output: floats within 1e-9, everything else
exactly. Hand-written published cases carry their own tolerance."""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from helpers import CONFORMANCE, DATA

from evalroom._serde import MISSING, to_json
from evalroom.agreement import agreement_report
from evalroom.audit import MemoryAuditSink
from evalroom.calibration import activate, create_draft, diff_values, rollback, snapshot
from evalroom.cli import CliIO, main
from evalroom.evalrun import CaseResult, EvalRun, compare_runs, gate, score_run, summarize
from evalroom.golden import golden_from_dict, promote_to_golden
from evalroom.jsonl import parse_jsonl
from evalroom.result import Err, Ok
from evalroom.review import Item, Review, parse_item, validate_review
from evalroom.rubric import Rubric, parse_rubric
from evalroom.rules import (
    PathRule,
    RuleFailure,
    contains_phrase,
    get_path,
    normalize_text,
    parse_rules,
    score_output,
)
from evalroom.stats import (
    cohen_kappa,
    fleiss_kappa,
    format_rate,
    interpret_kappa,
    median,
    wilson,
)

Case = dict[str, Any]


def vectors(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CONFORMANCE / name).read_text(encoding="utf-8"))
    return data


def same(actual: Any, expected: Any, tolerance: float, path: str, ignore: list[str]) -> str | None:
    """Deep comparison: floats within ``tolerance``, keys order-free, everything else exact."""
    if path in ignore:
        return None
    if isinstance(expected, bool) or isinstance(actual, bool):
        ok = isinstance(expected, bool) and isinstance(actual, bool) and actual == expected
        return None if ok else f"{path}: {actual!r} != {expected!r}"
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if float(expected).is_integer() and float(actual).is_integer():
            return None if actual == expected else f"{path}: {actual!r} != {expected!r}"
        return (
            None
            if math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance)
            else (f"{path}: {actual!r} != {expected!r}")
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return f"{path}: list length {len(actual) if isinstance(actual, list) else '-'} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            problem = same(a, e, tolerance, f"{path}[{i}]", ignore)
            if problem:
                return problem
        return None
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f"{path}: not an object: {actual!r}"
        for key in set(actual) | set(expected):
            child = f"{path}.{key}" if path else key
            if child in ignore:
                continue
            if key not in actual or key not in expected:
                return f"{child}: present on one side only"
            problem = same(actual[key], expected[key], tolerance, child, ignore)
            if problem:
                return problem
        return None
    return (
        None
        if actual == expected and type(actual) is type(expected)
        else (f"{path}: {actual!r} != {expected!r}")
    )


def check(actual: Any, case: Case, ignore: list[str] | None = None) -> None:
    tolerance = float(case.get("tolerance", 1e-9))
    expected = case.get("expected")
    target = json.loads(json.dumps(to_json(actual)))
    if "published" in case and isinstance(expected, dict) and isinstance(target, dict):
        target = {key: target.get(key) for key in expected}
    problem = same(target, expected, tolerance, "", ignore or [])
    assert problem is None, f"{case.get('name', case)}: {problem}"


def wilson_options(options: dict[str, Any] | None) -> dict[str, Any]:
    names = {"z": "z", "minN": "min_n", "maxWidth": "max_width"}
    return {names[key]: value for key, value in (options or {}).items()}


def ref(value: Any, key: str | None = None) -> Any:
    """Resolves ``"data/x.json"``, ``"data/x.jsonl"`` and ``"data/x.jsonl#id"`` references."""
    if not isinstance(value, str) or not value.startswith("data/"):
        return value
    file, _, row_id = value.partition("#")
    text = (CONFORMANCE / file).read_text(encoding="utf-8")
    if not file.endswith(".jsonl"):
        return json.loads(text)
    rows = parse_jsonl(text, file)
    if row_id:
        return next(row for row in rows if row.get("id") == row_id)
    if key == "outputs":
        return {row["caseId"]: row["output"] for row in rows}
    return rows


def rubric_of(raw: Any) -> Rubric:
    parsed = parse_rubric(ref(raw))
    assert isinstance(parsed, Ok), parsed
    return parsed.value


def reviews_of(raw: Any) -> list[Review]:
    reviews = []
    for row in ref(raw):
        parsed = validate_review(row, require_fail_details=False)
        assert isinstance(parsed, Ok), parsed
        reviews.append(parsed.value)
    return reviews


def item_of(raw: Any) -> Item:
    parsed = parse_item(ref(raw))
    assert isinstance(parsed, Ok), parsed
    return parsed.value


def rule_of(raw: dict[str, Any]) -> PathRule:
    """A path rule taken as written, without validation (score_output does not validate)."""
    return PathRule(
        path=raw["path"],
        op=raw["op"],
        value=raw.get("value", MISSING),
        flags=raw.get("flags"),
        code=raw.get("code"),
    )


def result_of(raw: dict[str, Any]) -> CaseResult:
    return CaseResult(
        case_id=raw["caseId"],
        passed=raw["passed"],
        failures=[RuleFailure(f["code"], f["path"], f["detail"]) for f in raw["failures"]],
        output=raw.get("output"),
    )


def run_of(raw: dict[str, Any]) -> EvalRun:
    return EvalRun(
        id=raw["id"],
        label=raw["label"],
        rubric_version=raw["rubricVersion"],
        results=[result_of(r) for r in raw["results"]],
        created_at=raw.get("createdAt"),
        tenant=raw.get("tenant"),
    )


def compact_results(case: Case) -> list[CaseResult]:
    passed, total, code = int(case["passed"]), int(case["total"]), case.get("code", "X")
    return [
        CaseResult(
            case_id=f"c{i}",
            passed=i < passed,
            failures=[] if i < passed else [RuleFailure(code, "", code.lower())],
            output=None,
        )
        for i in range(total)
    ]


def result_json(result: Ok[Any] | Err[Any], error_key: str = "error") -> dict[str, Any]:
    if isinstance(result, Ok):
        return {"ok": True, "value": to_json(result.value)}
    return {"ok": False, error_key: to_json(result.error)}


# ─── stats.json ─────────────────────────────────────────────────────────────

STATS = vectors("stats.json")


@pytest.mark.parametrize("case", STATS["wilson"], ids=lambda c: f"{c['k']}/{c['n']}")
def test_wilson(case: Case) -> None:
    check(wilson(case["k"], case["n"], **wilson_options(case.get("options"))), case)


@pytest.mark.parametrize("case", STATS["formatRate"], ids=lambda c: f"{c['k']}/{c['n']}")
def test_format_rate(case: Case) -> None:
    assert (
        format_rate(case["k"], case["n"], **wilson_options(case.get("options"))) == case["expected"]
    )


def test_fleiss_cohen_interpret_median() -> None:
    for case in STATS["fleissKappa"]:
        check(fleiss_kappa(case["rows"]), case)
    for case in STATS["cohenKappa"]:
        check(cohen_kappa(case["a"], case["b"]), case)
    for case in STATS["interpretKappa"]:
        assert interpret_kappa(case["kappa"]) == case["expected"], case
    for case in STATS["median"]:
        check(median(case["values"]), case)


# ─── agreement.json, golden.json ────────────────────────────────────────────

AGREEMENT = vectors("agreement.json")["cases"]
GOLDEN = vectors("golden.json")["cases"]


@pytest.mark.parametrize("case", AGREEMENT, ids=lambda c: c["name"])
def test_agreement(case: Case) -> None:
    options = case["options"]
    report = agreement_report(
        reviews_of(case["reviews"]),
        rubric_of(case["rubric"]),
        rubric_version=options.get("rubricVersion"),
        min_items=options.get("minItems", 10),
    )
    check(report.to_dict(), case)


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: c["name"])
def test_golden(case: Case) -> None:
    options = case["options"]
    kwargs: dict[str, Any] = {
        "rubric": rubric_of(case["rubric"]),
        "expected_rules": options["expectedRules"],
        "promoted_by": options["promotedBy"],
        "promoted_at": options.get("promotedAt"),
        "id": options.get("id"),
        "title": options.get("title"),
    }
    if "requireUnanimousPass" in options:
        kwargs["require_unanimous_pass"] = options["requireUnanimousPass"]
    if "checkReferenceOutput" in options:
        kwargs["check_reference_output"] = options["checkReferenceOutput"]
    result = promote_to_golden(item_of(case["item"]), reviews_of(case["reviews"]), **kwargs)
    check(result_json(result), case)


# ─── rules.json ─────────────────────────────────────────────────────────────

RULES = vectors("rules.json")


@pytest.mark.parametrize("case", RULES["scoreOutput"], ids=lambda c: c["name"])
def test_score_output(case: Case) -> None:
    check(score_output(case["output"], [rule_of(r) for r in case["rules"]]), case)


@pytest.mark.parametrize("case", RULES["parseRules"], ids=lambda c: c["name"])
def test_parse_rules(case: Case) -> None:
    parsed = parse_rules(case["input"])
    if isinstance(parsed, Ok):
        check({"ok": True, "value": parsed.value}, case)
        return
    expected = case["expected"]
    assert expected["ok"] is False
    assert len(parsed.error) == len(expected["issues"])
    for mine, theirs in zip(parsed.error, expected["issues"], strict=True):
        # Regex engines word their syntax errors differently; the prefix must match.
        if "invalid regex (" in theirs:
            assert mine.split("(")[0] == theirs.split("(")[0], (mine, theirs)
        else:
            assert mine == theirs


def test_text_helpers_and_paths() -> None:
    for case in RULES["normalizeText"]:
        assert normalize_text(case["input"]) == case["expected"], case
    for case in RULES["containsPhrase"]:
        assert contains_phrase(case["haystack"], case["needle"]) is case["expected"], case
    for case in RULES["getPath"]:
        found = get_path(case["value"], case["path"])
        check({"found": found.found, "value": found.value}, case)


# ─── evalrun.json ───────────────────────────────────────────────────────────

EVALRUN = vectors("evalrun.json")


@pytest.mark.parametrize("case", EVALRUN["scoreRun"], ids=lambda c: c["name"])
def test_score_run(case: Case) -> None:
    options = case["options"]
    evaluated = score_run(
        [golden_from_dict(row) for row in ref(case["cases"])],
        ref(case["outputs"], "outputs"),
        id=options["id"],
        label=options["label"],
        created_at=options.get("createdAt"),
        rubric_version=options.get("rubricVersion"),
    )
    check(evaluated, case)


def test_summarize_and_compare() -> None:
    for case in EVALRUN["summarize"]:
        check(summarize(compact_results(case)), case)
    for case in EVALRUN["compareRuns"]:
        check(compare_runs(run_of(case["base"]), run_of(case["candidate"])), case)


@pytest.mark.parametrize(
    "case",
    EVALRUN["gate"],
    ids=lambda c: c.get("name") or f"{c['passed']}/{c['total']}@{c['options']['minPassRate']}",
)
def test_gate(case: Case) -> None:
    options = case["options"]
    kwargs = {
        "min_pass_rate": options["minPassRate"],
        **wilson_options({k: v for k, v in options.items() if k != "minPassRate"}),
    }
    if case.get("error"):
        with pytest.raises(ValueError, match="must be in"):
            gate(compact_results(case), **kwargs)
    else:
        check(gate(compact_results(case), **kwargs), case)


# ─── calibration.json ───────────────────────────────────────────────────────

CALIBRATION = vectors("calibration.json")


def test_snapshot_and_diff() -> None:
    for case in CALIBRATION["snapshot"]:
        check(snapshot(case["input"]), case)
    for case in CALIBRATION["diffValues"]:
        assert diff_values(case["before"], case["after"]) == case["expected"]


STEP_ARGS = {
    "config": "config",
    "reason": "reason",
    "author": "author",
    "id": "id",
    "at": "at",
    "actor": "actor",
    "allowSelfActivation": "allow_self_activation",
}


@pytest.mark.parametrize("case", CALIBRATION["lifecycle"], ids=lambda c: c["name"])
def test_calibration_lifecycle(case: Case) -> None:
    from evalroom.store import MemoryStore

    store: MemoryStore[Any] = MemoryStore()
    audit = MemoryAuditSink()
    ops: dict[str, Callable[..., Any]] = {
        "createDraft": create_draft,
        "activate": activate,
        "rollback": rollback,
    }
    results = []
    for step in case["steps"]:
        kwargs = {STEP_ARGS[key]: value for key, value in step["input"].items()}
        results.append(result_json(ops[step["op"]](store, audit=audit, **kwargs)))
    actual = {
        "results": results,
        "versions": to_json(store.list_versions()),
        "audit": to_json(audit.list()),
    }
    check(actual, case)


# ─── cli/cases.json ─────────────────────────────────────────────────────────

CLI = vectors("cli/cases.json")["cases"]


@pytest.mark.parametrize("case", CLI, ids=lambda c: c["name"])
def test_cli(case: Case, tmp_path: Path) -> None:
    work = tmp_path / "data"
    shutil.copytree(DATA, work)
    out: list[str] = []
    io = CliIO(stdout=out.append, stderr=lambda _: None, env={"NO_COLOR": "1"}, cwd=str(work))
    code = main(case["args"], io)
    assert code == case["exit"]
    text = "".join(out)
    stdout = json.loads(text) if text.strip().startswith("{") else None
    problem = same(stdout, case["stdout"], 1e-9, "", case.get("ignore", []))
    assert problem is None, problem
