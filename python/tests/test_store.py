from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evalroom.audit import JsonlAuditSink
from evalroom.calibration import activate, create_draft, rollback
from evalroom.evalrun import CaseResult, EvalRun
from evalroom.golden import GoldenCase, GoldenProvenance
from evalroom.jsonl import JsonlParseError, parse_jsonl
from evalroom.result import Ok
from evalroom.review import Item
from evalroom.rules import PathRule, RuleFailure
from evalroom.store import (
    DuplicateIdError,
    EvalStore,
    JsonlStore,
    MemoryStore,
    format_run_jsonl,
    parse_run_jsonl,
    read_run_file,
    write_run_file,
)
from helpers import Clock

golden = GoldenCase(
    id="golden-t-1",
    item_id="t-1",
    input={"message": "hi"},
    reference_output={"reply": "hello"},
    expected_rules=[PathRule("reply", "exists")],
    provenance=GoldenProvenance(["r1", "r2", "r3"], "m", "2026-10-20T00:00:00.000Z"),
    rubric_version="v1",
    tenant="acme",
)

run = EvalRun(
    id="base",
    label="baseline",
    rubric_version="v1",
    created_at="2026-10-21T00:00:00.000Z",
    results=[
        CaseResult("golden-t-1", True, [], {"reply": "hello"}),
        CaseResult("golden-t-2", False, [RuleFailure("X", "reply", "bad")], {"reply": "?"}),
    ],
)


def exercise(store: EvalStore, clock: Clock) -> None:
    store.add_item(Item(id="t-1", output={"reply": "hello"}, tenant="acme"))
    store.add_item(Item(id="t-2", output={"reply": "bye"}))
    for row in [
        *clock.triple("t-1", "PASS", "PASS", "PASS", tenant="acme"),
        clock.review("t-2", "tone", "FAIL"),
    ]:
        store.add_review(row)
    store.add_golden(golden)
    store.add_run(run)

    found = store.get_item("t-1")
    assert found is not None
    assert found.output == {"reply": "hello"}
    assert store.get_item("nope") is None
    assert [i.id for i in store.list_items(tenant="acme")] == ["t-1"]
    assert [r.id for r in store.list_reviews()] == ["r1", "r2", "r3", "r4"]
    assert [r.id for r in store.list_reviews(item_id="t-1", lens="tone")] == ["r2"]
    assert len(store.list_reviews(tenant="acme")) == 3
    assert store.get_golden("golden-t-1") == golden
    assert store.list_golden(tenant="other") == []
    assert store.get_run("base") == run
    assert [r.id for r in store.list_runs()] == ["base"]

    with pytest.raises(DuplicateIdError):
        store.add_review(clock.review("t-1", "tone", "PASS", id="r1"))
    with pytest.raises(DuplicateIdError):
        store.add_golden(golden)
    with pytest.raises(DuplicateIdError):
        store.add_run(run)


def test_memory_store_contract_and_copies(clock: Clock) -> None:
    store: MemoryStore[Any] = MemoryStore()
    exercise(store, clock)
    item = store.get_item("t-1")
    assert item is not None
    item.output = "mutated"
    again = store.get_item("t-1")
    assert again is not None
    assert again.output == {"reply": "hello"}


def test_jsonl_store_contract_and_round_trip(tmp_path: Path, clock: Clock) -> None:
    exercise(JsonlStore(tmp_path), clock)
    reopened: JsonlStore[Any] = JsonlStore(tmp_path)
    assert [r.id for r in reopened.list_reviews()] == ["r1", "r2", "r3", "r4"]
    assert reopened.get_run("base") == run
    text = (tmp_path / "reviews.jsonl").read_text(encoding="utf-8")
    assert len(text.strip().split("\n")) == 4
    assert '"itemId":"t-1"' in text


def test_jsonl_calibration_history_is_append_only(tmp_path: Path) -> None:
    store: JsonlStore[dict[str, str]] = JsonlStore(tmp_path)
    audit = JsonlAuditSink(tmp_path / "audit.jsonl")
    for model in ("a", "b"):
        draft = create_draft(store, config={"model": model}, reason="r", author="ana", audit=audit)
        assert isinstance(draft, Ok)
        activate(store, id=draft.value.id, actor="ben", reason="ok", audit=audit)
    rollback(store, actor="ben", reason="regressed", audit=audit)
    fresh: JsonlStore[dict[str, str]] = JsonlStore(tmp_path)
    assert [(v.id, v.status) for v in fresh.list_versions()] == [
        ("cal-1", "active"),
        ("cal-2", "rolled_back"),
    ]
    lines = (tmp_path / "calibration.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) > 2
    assert [e.action for e in JsonlAuditSink(tmp_path / "audit.jsonl").list()] == [
        "calibration.draft",
        "calibration.activate",
        "calibration.draft",
        "calibration.activate",
        "calibration.rollback",
    ]


def test_unsafe_run_ids_are_refused(tmp_path: Path) -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="file name"):
        JsonlStore(tmp_path).add_run(replace(run, id="../escape"))


def test_jsonl_skips_blank_lines_accepts_crlf_bom_and_reports_line() -> None:
    assert parse_jsonl('﻿{"a":1}\r\n\r\n{"a":2}\n') == [{"a": 1}, {"a": 2}]
    with pytest.raises(JsonlParseError) as caught:
        parse_jsonl('{"a":1}\n{oops}\n', "reviews.jsonl")
    assert caught.value.line == 2
    assert str(caught.value).startswith("reviews.jsonl:2: ")
    with pytest.raises(JsonlParseError):
        parse_jsonl("NaN")


def test_run_file_round_trip_and_headerless(tmp_path: Path) -> None:
    path = tmp_path / "out" / "run.jsonl"
    write_run_file(path, run)
    assert read_run_file(path) == run
    assert '"kind":"run"' in format_run_jsonl(run).split("\n")[0]
    headerless = tmp_path / "candidate.jsonl"
    headerless.write_text('{"caseId":"a","passed":true}\n', encoding="utf-8")
    assert read_run_file(headerless) == EvalRun(
        id="candidate",
        label="candidate",
        rubric_version="unknown",
        results=[CaseResult("a", True, [], None)],
    )


def test_malformed_result_lines_and_duplicates() -> None:
    with pytest.raises(JsonlParseError, match=r"r\.jsonl:1: passed"):
        parse_run_jsonl('{"caseId":"a","passed":"yes"}', "r.jsonl")
    with pytest.raises(JsonlParseError, match="duplicate"):
        parse_run_jsonl('{"caseId":"a","passed":true}\n{"caseId":"a","passed":false}')
    with pytest.raises(JsonlParseError, match="header must be the first line"):
        parse_run_jsonl('{"caseId":"a","passed":true}\n{"kind":"run","id":"x"}')
