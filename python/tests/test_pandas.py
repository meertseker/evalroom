from __future__ import annotations

import pytest

from helpers import DATA, ROOT, Clock, rubric

pytest.importorskip("pandas")

from evalroom.agreement import agreement_report
from evalroom.pandas import to_dataframe
from evalroom.store import read_run_file


def test_reviews_and_runs(clock: Clock) -> None:
    reviews = clock.triple("i1", "PASS", "FAIL", "PASS")
    frame = to_dataframe(reviews)
    assert list(frame["item_id"]) == ["i1", "i1", "i1"]
    assert frame.loc[1, "error_class"] == "DISMISSIVE"
    run = to_dataframe(read_run_file(DATA / "run-base.jsonl"))
    assert len(run) == 11
    assert int(run["passed"].sum()) == 7
    assert "WINDOW_30" in run["failure_codes"].tolist()[1]


def test_agreement_disagreements(clock: Clock) -> None:
    rows = [r for i in range(10) for r in clock.triple(f"i{i}", "PASS", "FAIL", "PASS")]
    frame = to_dataframe(agreement_report(rows, rubric))
    assert len(frame) == 30
    assert set(frame.columns) >= {"item_id", "lens", "verdict", "reviewer"}


def test_judge_check_disagreements() -> None:
    pytest.importorskip("inspect_ai")
    from evalroom.inspect import judge_check

    report = judge_check(ROOT / "python" / "examples" / "inspect_judge" / "judge_fixture.eval")
    frame = to_dataframe(report)
    assert len(frame) == 8
    assert list(frame.columns[:4]) == ["sample_id", "epoch", "judge", "human"]


def test_rejects_unknown_records() -> None:
    with pytest.raises(TypeError):
        to_dataframe([object()])
