from __future__ import annotations

from typing import Any

from evalroom.audit import MemoryAuditSink
from evalroom.golden import (
    GoldenCase,
    GoldenProvenance,
    PromotionRefusal,
    golden_from_dict,
    promote_and_record,
    promote_to_golden,
)
from evalroom.result import Err, Ok, Result
from evalroom.review import Item
from evalroom.rules import PathRule
from helpers import Clock, rubric

item = Item(
    id="t-1",
    input={"message": "Where is my refund?"},
    output={"reply": "Your refund was issued on 3 October and takes 5–7 business days."},
    tenant="acme",
)

RULES = [PathRule("reply", "notContains", ["guarantee"], code="PROMISE")]
OPTIONS: dict[str, Any] = {
    "rubric": rubric,
    "expected_rules": RULES,
    "promoted_by": "maintainer",
    "promoted_at": "2026-10-20T10:00:00.000Z",
}


def codes(result: Result[GoldenCase, list[PromotionRefusal]]) -> list[str]:
    return [] if isinstance(result, Ok) else [refusal.code for refusal in result.error]


def test_promotes_and_freezes_input_output_and_provenance(clock: Clock) -> None:
    result = promote_to_golden(item, clock.triple("t-1", "PASS", "PASS", "PASS"), **OPTIONS)
    assert isinstance(result, Ok)
    assert result.value == GoldenCase(
        id="golden-t-1",
        item_id="t-1",
        input=item.input,
        reference_output=item.output,
        expected_rules=RULES,
        provenance=GoldenProvenance(
            review_ids=["r1", "r2", "r3"],
            promoted_by="maintainer",
            promoted_at="2026-10-20T10:00:00.000Z",
        ),
        rubric_version="v1",
        tenant="acme",
    )
    assert result.value.input is not item.input
    assert golden_from_dict(result.value.to_dict()) == result.value


def test_refuses_and_names_the_missing_lens(clock: Clock) -> None:
    result = promote_to_golden(
        item,
        [clock.review("t-1", "accuracy", "PASS"), clock.review("t-1", "tone", "PASS")],
        **OPTIONS,
    )
    assert isinstance(result, Err)
    assert result.error[0].code == "MISSING_LENSES"
    assert result.error[0].lenses == ["policy"]
    assert result.error[0].message == "no current v1 verdict for lens: policy"


def test_split_verdict_refused_by_default(clock: Clock) -> None:
    reviews = clock.triple("t-1", "PASS", "NEEDS_REVIEW", "PASS")
    refused = promote_to_golden(item, reviews, **OPTIONS)
    assert codes(refused) == ["NOT_UNANIMOUS_PASS"]
    assert isinstance(refused, Err)
    assert refused.error[0].message == "not every lens passed: tone=NEEDS_REVIEW"
    allowed = promote_to_golden(item, reviews, **{**OPTIONS, "require_unanimous_pass": False})
    assert isinstance(allowed, Ok)


def test_latest_verdict_per_lens_other_items_and_versions_ignored(clock: Clock) -> None:
    reviews = [
        *clock.triple("t-1", "PASS", "FAIL", "PASS"),
        clock.review("t-1", "tone", "PASS"),
        clock.review("t-2", "tone", "FAIL"),
        clock.review("t-1", "policy", "FAIL", rubric_version="v0"),
    ]
    result = promote_to_golden(item, reviews, **OPTIONS)
    assert isinstance(result, Ok)
    assert result.value.provenance.review_ids == ["r1", "r4", "r3"]


def test_older_rubric_reviews_do_not_count(clock: Clock) -> None:
    reviews = clock.triple("t-1", "PASS", "PASS", "PASS", rubric_version="v0")
    assert codes(promote_to_golden(item, reviews, **OPTIONS)) == ["MISSING_LENSES"]


def test_every_refusal_at_once(clock: Clock) -> None:
    result = promote_to_golden(item, [], **{**OPTIONS, "expected_rules": [], "promoted_by": " "})
    assert codes(result) == ["MISSING_LENSES", "NO_RULES", "NO_PROMOTER"]
    invalid = promote_to_golden(
        item,
        clock.triple("t-1", "PASS", "PASS", "PASS"),
        **{**OPTIONS, "expected_rules": [{"path": "reply", "op": "between", "value": "x"}]},
    )
    assert codes(invalid) == ["INVALID_RULES"]


def test_rules_that_reject_the_reviewed_output(clock: Clock) -> None:
    result = promote_to_golden(
        item,
        clock.triple("t-1", "PASS", "PASS", "PASS"),
        **{**OPTIONS, "expected_rules": [PathRule("reply", "contains", "30 days", code="WINDOW")]},
    )
    assert codes(result) == ["REFERENCE_OUTPUT_FAILS"]
    assert isinstance(result, Err)
    assert "WINDOW" in result.error[0].message


def test_promote_and_record_audits_success_only(clock: Clock) -> None:
    audit = MemoryAuditSink()
    promote_and_record(item, [], audit, **OPTIONS)
    assert audit.list() == []
    promote_and_record(item, clock.triple("t-1", "PASS", "PASS", "PASS"), audit, **OPTIONS)
    events = audit.list()
    assert len(events) == 1
    event = events[0]
    assert (event.action, event.actor, event.target, event.at, event.tenant) == (
        "golden.promote",
        "maintainer",
        "golden-t-1",
        "2026-10-20T10:00:00.000Z",
        "acme",
    )
    assert event.after == {
        "itemId": "t-1",
        "rubricVersion": "v1",
        "reviewIds": ["r1", "r2", "r3"],
        "rules": 1,
    }
