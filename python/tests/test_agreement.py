from __future__ import annotations

import pytest

from evalroom.agreement import KappaStat, agreement_report
from evalroom.review import Review
from evalroom.rubric import Verdict, define_rubric
from helpers import Clock, rubric


def test_only_complete_items_and_latest_verdicts(clock: Clock) -> None:
    report = agreement_report(
        [
            clock.review("i1", "accuracy", "FAIL"),
            *clock.triple("i1", "PASS", "PASS", "PASS"),
            clock.review("i2", "accuracy", "PASS"),
            clock.review("i2", "tone", "PASS"),
        ],
        rubric,
    )
    assert report.items == 1
    assert report.incomplete_items == 1
    raw = report.raw_agreement
    assert (raw.p, raw.k, raw.n, raw.decidable) == (1, 1, 1, False)
    assert (report.prevalence["PASS"].p, report.prevalence["PASS"].k) == (1, 3)
    assert (report.prevalence["FAIL"].p, report.prevalence["FAIL"].n) == (0, 3)


def test_within_one_rubric_version(clock: Clock) -> None:
    rows = [
        *clock.triple("i1", "PASS", "PASS", "PASS", rubric_version="v0"),
        *clock.triple("i2", "PASS", "FAIL", "PASS"),
    ]
    current = agreement_report(rows, rubric)
    assert current.rubric_version == "v1"
    assert current.items == 1
    assert current.excluded_reviews == 3
    older = agreement_report(rows, rubric, rubric_version="v0")
    assert older.items == 1
    assert older.disagreements == []


def test_kappa_none_below_min_items(clock: Clock) -> None:
    nine = [
        r
        for i in range(9)
        for r in clock.triple(f"i{i}", "PASS", "PASS", "FAIL" if i % 2 else "PASS")
    ]
    small = agreement_report(nine, rubric)
    assert small.fleiss_kappa == KappaStat(
        value=None, band=None, n=9, reason="9 complete items; kappa needs at least 10"
    )
    assert small.pairs[0].cohen_kappa.value is None
    assert small.interpretation == "Kappa not reported: 9 complete items; kappa needs at least 10."

    ten: list[Review] = []
    for i in range(10):
        v: Verdict = "FAIL" if i % 2 else "PASS"
        ten.extend(clock.triple(f"i{i}", v, v, v))
    enough = agreement_report(ten, rubric)
    assert enough.fleiss_kappa.value == pytest.approx(1, abs=1e-10)
    assert enough.fleiss_kappa.band == "almost perfect"
    assert all(pair.cohen_kappa.value == 1 for pair in enough.pairs)


def test_undefined_kappa_when_every_verdict_is_identical(clock: Clock) -> None:
    rows = [r for i in range(12) for r in clock.triple(f"i{i}", "PASS", "PASS", "PASS")]
    report = agreement_report(rows, rubric)
    assert report.fleiss_kappa.value is None
    assert "undefined" in (report.fleiss_kappa.reason or "")
    assert report.raw_agreement.p == 1


def test_hand_computed_kappa_and_dominant_verdict(clock: Clock) -> None:
    # 10 items, 3 lenses: 6 unanimous PASS, 2 unanimous FAIL, 2 split 2 PASS / 1 FAIL.
    # P̄ = (8·1 + 2·(1/3)) / 10 = 0.86667; p_PASS = 22/30, p_FAIL = 8/30;
    # P_e = 0.53778 + 0.07111 = 0.60889; κ = (0.86667 - 0.60889) / 0.39111 = 0.65909.
    rows = [
        *[r for i in range(6) for r in clock.triple(f"p{i}", "PASS", "PASS", "PASS")],
        *clock.triple("f1", "FAIL", "FAIL", "FAIL"),
        *clock.triple("f2", "FAIL", "FAIL", "FAIL"),
        *clock.triple("s1", "PASS", "FAIL", "PASS"),
        *clock.triple("s2", "PASS", "PASS", "FAIL"),
    ]
    report = agreement_report(rows, rubric)
    assert report.fleiss_kappa.value == pytest.approx(0.65909, abs=5e-5)
    assert report.fleiss_kappa.band == "substantial"
    assert (report.raw_agreement.k, report.raw_agreement.n) == (8, 10)
    assert [d.item_id for d in report.disagreements] == ["s1", "s2"]
    tone = report.disagreements[0].verdicts[1]
    assert (tone.lens, tone.verdict, tone.error_class) == ("tone", "FAIL", "DISMISSIVE")


def test_pairs_confusion_and_median_seconds(clock: Clock) -> None:
    report = agreement_report(
        [
            *clock.triple("i1", "PASS", "PASS", "FAIL", seconds=60),
            *clock.triple("i2", "PASS", "FAIL", "FAIL", seconds=180),
        ],
        rubric,
    )
    assert [(p.a, p.b, p.agreement.k, p.agreement.n) for p in report.pairs] == [
        ("accuracy", "tone", 1, 2),
        ("accuracy", "policy", 0, 2),
        ("tone", "policy", 1, 2),
    ]
    assert report.pairs[1].confusion["PASS"]["FAIL"] == 2
    assert report.median_seconds == 120
    assert report.median_seconds_by_lens == {"accuracy": 120, "tone": 120, "policy": 120}


def test_single_lens_rubric_has_no_kappa(clock: Clock) -> None:
    solo = define_rubric(version="v1", lenses=["accuracy"], error_classes=[])
    rows = [clock.review(f"i{i}", "accuracy", "PASS") for i in range(12)]
    report = agreement_report(rows, solo)
    assert report.fleiss_kappa.reason == "kappa needs at least two lenses"
    assert report.pairs == []


def test_report_json_uses_camel_case_and_omits_absent_reasons(clock: Clock) -> None:
    rows = [r for i in range(10) for r in clock.triple(f"i{i}", "PASS", "FAIL", "PASS")]
    data = agreement_report(rows, rubric).to_dict()
    assert data["rubricVersion"] == "v1"
    assert set(data["rawAgreement"]) == {"p", "low", "high", "k", "n", "decidable"}
    assert "medianSecondsByLens" in data
    assert data["fleissKappa"]["value"] is not None
    assert "reason" not in data["fleissKappa"]
    assert data["disagreements"][0]["verdicts"][1]["reviewId"] == "r2"
