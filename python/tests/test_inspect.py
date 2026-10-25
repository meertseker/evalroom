"""The Inspect extension, against the committed fixture (a real inspect_ai log)."""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("inspect_ai")

from inspect_ai.log import EvalLog, ProvenanceData, edit_score, read_eval_log, write_eval_log
from inspect_ai.scorer import SampleScore, Score, ScoreEdit

from evalroom.cli import EXIT, CliIO, main
from evalroom.inspect import (
    UNDECIDABLE_WARNING,
    HumanLabel,
    InspectLogError,
    ScorerNotFound,
    compare_logs,
    gate_log,
    judge_check,
    label_of,
    load_human_labels,
    outcome_of,
    scored_samples,
    wilson_accuracy,
)
from evalroom.inspect._values import UnreadableValue
from helpers import ROOT

EXAMPLE = ROOT / "python" / "examples" / "inspect_judge"
FIXTURE = EXAMPLE / "judge_fixture.eval"


@pytest.fixture(scope="module")
def log() -> EvalLog:
    return read_eval_log(str(FIXTURE))


def fresh() -> EvalLog:
    return read_eval_log(str(FIXTURE))


def questions() -> list[Any]:
    sys.path.insert(0, str(EXAMPLE))
    try:
        import make_fixture  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return list(make_fixture.QUESTIONS)


def cli(args: list[str], cwd: Path = ROOT / "python") -> tuple[int, str, str]:
    out: list[str] = []
    err: list[str] = []
    io = CliIO(stdout=out.append, stderr=err.append, env={"NO_COLOR": "1"}, cwd=str(cwd))
    return main(args, io), "".join(out), "".join(err)


# ─── value mapping ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "fail", "passing", "exclude"),
    [
        ("C", True, True, True),
        ("I", False, False, False),
        ("N", False, False, False),
        ("P", False, True, None),
        (1, True, True, True),
        (0.0, False, False, False),
        (0.5, False, True, None),
        (True, True, True, True),
        ("yes", True, True, True),
        ("false", False, False, False),
        ("1", True, True, True),
        (math.nan, None, None, None),
    ],
)
def test_outcomes_follow_inspect_conventions(
    value: object, fail: bool | None, passing: bool | None, exclude: bool | None
) -> None:
    assert outcome_of(value, "fail") is fail
    assert outcome_of(value, "pass") is passing
    assert outcome_of(value, "exclude") is exclude


def test_unreadable_values_and_labels() -> None:
    for bad in ([1, 2], {"a": 1}, "maybe", 2):
        with pytest.raises(UnreadableValue):
            outcome_of(bad)
    assert label_of("c") == "C"
    assert label_of("P") == "I"
    assert label_of("P", "pass") == "C"
    assert label_of("P", "exclude") is None
    assert label_of(math.nan) is None
    assert label_of(False) == "I"
    assert label_of("excellent") == "excellent"


# ─── reading Score.history ──────────────────────────────────────────────────


def test_history_gives_the_original_and_the_edit(log: EvalLog) -> None:
    samples = {s.sample_id: s for s in scored_samples(log, "model_graded_qa")}
    assert len(samples) == 80
    corrected = samples["q68"]  # judge accepted "Au" for silver
    assert (corrected.original, corrected.current, corrected.edited) == ("C", "I", True)
    assert corrected.edit_reason == "Au is gold. Silver is Ag."
    assert corrected.editor in ("ana", "ben", "chen")
    assert "GRADE: C" in (corrected.explanation or "")
    assert corrected.input == "What is the chemical symbol for silver?"
    assert corrected.target == "Ag"
    edited = sum(1 for s in samples.values() if s.edited)
    confirmed = sum(1 for s in samples.values() if s.edited and s.original == s.current)
    assert (edited, confirmed) == (60, 50)


def test_several_edits_keep_the_original_and_use_the_last_reason() -> None:
    copy = fresh()
    for value, reason in (("I", "first look"), ("C", "second look")):
        edit_score(
            copy,
            "q01",
            "model_graded_qa",
            ScoreEdit(value=value, provenance=ProvenanceData(author="dee", reason=reason)),
        )
    sample = next(s for s in scored_samples(copy, "model_graded_qa") if s.sample_id == "q01")
    assert (sample.original, sample.current, sample.edit_reason, sample.editor) == (
        "C",
        "C",
        "second look",
        "dee",
    )


def test_a_score_created_by_an_edit_has_no_judge_label(tmp_path: Path) -> None:
    copy = fresh()
    assert copy.samples is not None
    copy.samples[0].scores = {
        **(copy.samples[0].scores or {}),
        "model_graded_qa": Score(
            value="C",
            history=[ScoreEdit(value="C", provenance=ProvenanceData(author="dee", reason="new"))],
        ),
    }
    report = judge_check(copy)
    assert report.excluded == 1


# ─── judge_check on the fixture ─────────────────────────────────────────────


def test_judge_check_on_the_fixture(log: EvalLog) -> None:
    report = judge_check(log, scorer="model_graded_qa")
    assert (report.samples, report.reviewed, report.not_reviewed, report.excluded) == (
        80,
        60,
        20,
        0,
    )
    assert (report.agreement.k, report.agreement.n) == (52, 60)
    assert report.agreement.low == pytest.approx(0.7583, abs=5e-4)
    assert report.agreement.high == pytest.approx(0.9309, abs=5e-4)
    assert (report.judge_wrong.k, report.judge_wrong.n) == (8, 60)
    assert report.kappa.value == pytest.approx(0.64497, abs=5e-5)
    assert report.kappa.band == "substantial"
    assert report.labels == ["C", "I"]
    assert report.confusion == {"C": {"C": 41, "I": 5}, "I": {"C": 3, "I": 11}}
    assert report.total_disagreements == 8
    assert [d.sample_id for d in report.disagreements] == [
        "q55", "q57", "q58", "q68", "q69", "q71", "q72", "q73",
    ]  # fmt: skip
    assert report.decision == "undecidable"
    assert report.warning == UNDECIDABLE_WARNING
    assert "collect more labels" in report.reason
    assert report.exit_code == EXIT.undecidable
    assert report.judge == "mockllm/model"
    first = report.disagreements[0]
    assert (first.judge, first.human, first.target) == ("I", "C", "Canberra")
    assert first.human_reason == "Answer says Canberra; the extra clause is not a different answer."


def test_partial_credit_policies(log: EvalLog) -> None:
    passing = judge_check(log, partial="pass")
    assert (passing.agreement.k, passing.agreement.n) == (51, 60)
    excluded = judge_check(log, partial="exclude")
    assert (excluded.agreement.k, excluded.agreement.n, excluded.excluded) == (50, 57, 3)
    with pytest.raises(ValueError, match="partial"):
        judge_check(log, partial="half")  # type: ignore[arg-type]


def test_assuming_unedited_agreement_is_opt_in_and_labelled(log: EvalLog) -> None:
    report = judge_check(log, assume_unedited_agree=True)
    assert (report.assumed, report.not_reviewed, report.reviewed) == (20, 0, 60)
    assert (report.agreement.k, report.agreement.n) == (72, 80)
    assert report.decision == "acceptable"


def test_thresholds_change_the_decision(log: EvalLog) -> None:
    assert judge_check(log, min_agreement=0.7).decision == "acceptable"
    assert judge_check(log, min_agreement=0.95).decision == "not_acceptable"
    small = judge_check(log, min_n=100)
    assert small.decision == "undecidable"
    assert small.reason.startswith("n=60 is below minN=100")
    assert len(judge_check(log, top=3).disagreements) == 3


def test_a_labels_file_replaces_the_edits(tmp_path: Path, log: EvalLog) -> None:
    rows = [
        {"sampleId": f"q{i + 1:02d}", "label": q.truth, "reason": "reference check"}
        for i, q in enumerate(questions())
    ]
    jsonl = tmp_path / "labels.jsonl"
    jsonl.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    report = judge_check(log, human=jsonl)
    assert report.source == "labels"
    assert (report.reviewed, report.not_reviewed) == (80, 0)
    assert (report.agreement.k, report.agreement.n) == (68, 80)
    assert report.total_disagreements == 12

    csv_path = tmp_path / "labels.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sampleId", "epoch", "label"])
        writer.writeheader()
        for row in rows[:30]:
            writer.writerow({"sampleId": row["sampleId"], "epoch": 1, "label": row["label"]})
        writer.writerow({"sampleId": "nope", "epoch": 1, "label": "C"})
    partial = judge_check(log, human=csv_path)
    assert (partial.reviewed, partial.not_reviewed, partial.unmatched_labels) == (30, 50, 1)
    labels = load_human_labels(csv_path)
    assert labels[0] == HumanLabel(sample_id="q01", label="C", epoch=1)
    in_memory = judge_check(log, human=[HumanLabel("q68", "I")])
    assert (in_memory.agreement.k, in_memory.agreement.n) == (0, 1)


def test_bad_labels_files(tmp_path: Path, log: EvalLog) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"sampleId": "q01"}\n', encoding="utf-8")
    with pytest.raises(InspectLogError, match="needs a label"):
        judge_check(log, human=bad)
    with pytest.raises(InspectLogError, match="cannot read"):
        judge_check(log, human=tmp_path / "missing.jsonl")
    headless = tmp_path / "bad.csv"
    headless.write_text("q01,C\n", encoding="utf-8")
    with pytest.raises(InspectLogError, match="header"):
        load_human_labels(headless)


def test_report_json_shape(log: EvalLog) -> None:
    data = judge_check(log).to_dict()
    assert {"agreement", "judgeWrong", "kappa", "confusion", "disagreements", "decision"} <= set(
        data
    )
    assert data["notReviewed"] == 20
    assert data["disagreements"][0]["sampleId"] == "q55"
    assert data["disagreements"][0]["humanReason"].startswith("Answer says Canberra")


# ─── gate and compare on logs ───────────────────────────────────────────────


def test_gate_log_uses_the_corrected_scores(log: EvalLog) -> None:
    result = gate_log(log, min_pass_rate=0.6)
    assert (result.gate.interval.k, result.gate.interval.n) == (60, 80)
    assert result.gate.decision == "pass"
    assert result.reviewed_edits == 60
    assert gate_log(log, min_pass_rate=0.9).gate.decision == "fail"
    excluded = gate_log(log, min_pass_rate=0.6, partial="exclude")
    assert (excluded.gate.interval.n, excluded.excluded) == (77, 3)
    with pytest.raises(ScorerNotFound):
        gate_log(log, scorer="nope")


def test_compare_logs_matches_samples_by_id(tmp_path: Path) -> None:
    candidate = fresh()
    assert candidate.samples is not None
    for sample in candidate.samples[:5]:
        assert sample.scores is not None
        sample.scores["model_graded_qa"].value = "I"
    for sample in candidate.samples[-4:]:
        assert sample.scores is not None
        sample.scores["model_graded_qa"].value = "C"
    path = tmp_path / "candidate.eval"
    write_eval_log(candidate, str(path))
    result = compare_logs(FIXTURE, path)
    assert [row.case_id for row in result.comparison.broken] == ["q01", "q02", "q03", "q04", "q05"]
    assert [row.case_id for row in result.comparison.fixed] == ["q77", "q78", "q79", "q80"]
    assert result.base.label != result.candidate.label


# ─── metric and registration ────────────────────────────────────────────────


def run(metric: Any, scores: list[SampleScore]) -> Any:
    return metric(scores)


def test_wilson_accuracy_metric() -> None:
    metric = wilson_accuracy()
    scores = [
        SampleScore(score=Score(value=v), sample_id=i)
        for i, v in enumerate(["C"] * 45 + ["I"] * 4 + ["P"])
    ]
    value = run(metric, scores)
    assert isinstance(value, dict)
    assert value["n"] == 50
    assert value["wilson_accuracy"] == pytest.approx(0.9)
    assert value["lower"] == pytest.approx(0.7864, abs=5e-4)
    assert value["decidable"] == 1.0
    excluded = run(wilson_accuracy(partial="exclude"), scores)
    assert isinstance(excluded, dict)
    assert excluded["n"] == 49
    assert excluded["decidable"] == 0.0


def test_inspect_loads_the_metric_by_name_through_the_entry_point(log: EvalLog) -> None:
    from inspect_ai.util import registry_create

    metric = registry_create("metric", "evalroom/wilson_accuracy", partial="pass")
    value = run(metric, [SampleScore(score=Score(value="P"))])
    assert isinstance(value, dict)
    assert value["wilson_accuracy"] == 1.0
    assert log.results is not None
    metrics = log.results.scores[0].metrics
    assert metrics["wilson_accuracy"].value == pytest.approx(0.75)
    assert metrics["n"].value == 80
    spec = log.eval.scorers or []
    assert any(
        m.name == "evalroom/wilson_accuracy" for m in spec[0].metrics or [] if hasattr(m, "name")
    )


# ─── CLI ────────────────────────────────────────────────────────────────────


def test_cli_judge_check_text_and_exit_code() -> None:
    code, out, _ = cli(
        ["judge-check", "examples/inspect_judge/judge_fixture.eval", "--scorer", "model_graded_qa"]
    )
    assert code == EXIT.undecidable
    assert "Agreement      87% (52/60) · 76%–93%" in out
    assert "Judge wrong    13% (8/60) · 7%–24%" in out
    assert "Warning: do not trust this judge on this evidence yet." in out
    assert "60 reviewed (a human edit in Score.history) · 20 not reviewed, excluded" in out
    assert "q68  judge C, human I" in out


def test_cli_judge_check_json_and_flags() -> None:
    code, out, _ = cli(
        [
            "judge-check",
            "examples/inspect_judge/judge_fixture.eval",
            "--json",
            "--min-agreement",
            "0.7",
        ]
    )
    assert code == EXIT.ok
    assert json.loads(out)["decision"] == "acceptable"
    code, out, _ = cli(
        ["judge-check", "examples/inspect_judge/judge_fixture.eval", "--assume-unedited-agree"]
    )
    assert code == EXIT.ok
    assert "--assume-unedited-agree: 20 unreviewed samples counted as agreements" in out
    assert (
        cli(
            ["judge-check", "examples/inspect_judge/judge_fixture.eval", "--min-agreement", "0.99"]
        )[0]
        == EXIT.fail
    )
    assert (
        cli(["judge-check", "examples/inspect_judge/judge_fixture.eval", "--partial", "half"])[0]
        == EXIT.usage
    )
    assert (
        cli(["judge-check", "examples/inspect_judge/judge_fixture.eval", "--scorer", "nope"])[0]
        == EXIT.usage
    )
    assert cli(["judge-check"])[0] == EXIT.usage
    assert cli(["judge-check", "missing.eval"])[0] == EXIT.data


def test_cli_gate_and_compare_on_logs(tmp_path: Path) -> None:
    code, out, _ = cli(
        ["gate", "examples/inspect_judge/judge_fixture.eval", "--threshold", "0.6", "--json"]
    )
    assert code == EXIT.ok
    data = json.loads(out)
    assert (data["decision"], data["interval"]["k"], data["scorer"]) == (
        "pass",
        60,
        "model_graded_qa",
    )
    code, out, _ = cli(["gate", "examples/inspect_judge/judge_fixture.eval"])
    assert code == EXIT.fail
    assert "scorer model_graded_qa" in out
    code, out, _ = cli(
        [
            "compare",
            "examples/inspect_judge/judge_fixture.eval",
            "examples/inspect_judge/judge_fixture.eval",
            "--json",
        ]
    )
    assert code == EXIT.ok
    assert json.loads(out)["broken"] == []


def test_cli_reads_json_logs(tmp_path: Path, log: EvalLog) -> None:
    path = tmp_path / "log.json"
    write_eval_log(log, str(path), format="json")
    code, out, _ = cli(["judge-check", str(path), "--json"])
    assert code == EXIT.undecidable
    assert json.loads(out)["agreement"]["k"] == 52


def test_cli_without_the_extra_exits_64(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        m
        for m in sys.modules
        if m == "inspect_ai" or m.startswith(("inspect_ai.", "evalroom.inspect"))
    ]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "inspect_ai", None)
    code, _, err = cli(["judge-check", "examples/inspect_judge/judge_fixture.eval"])
    assert code == EXIT.usage
    assert "install evalroom[inspect]" in err
    assert cli(["gate", "examples/inspect_judge/judge_fixture.eval"])[0] == EXIT.usage
