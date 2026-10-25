from __future__ import annotations

import dataclasses

import pytest

from evalroom.result import Err, Ok
from evalroom.review import (
    IncompleteItem,
    complete_items,
    incomplete_items,
    latest_per_lens,
    missing_lenses,
    normalize_seconds,
    parse_item,
    validate_review,
)
from evalroom.rubric import (
    ErrorClassDef,
    RubricError,
    define_rubric,
    error_classes_for_lens,
    parse_rubric,
)
from helpers import Clock, rubric


def test_define_rubric_is_immutable_and_lists_classes_per_lens() -> None:
    assert rubric.version == "v1"
    assert isinstance(rubric.lenses, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rubric.version = "v2"  # type: ignore[misc]
    codes = [entry.code for entry in error_classes_for_lens(rubric, "accuracy")]
    assert codes == ["WRONG_FACT", "UNSUPPORTED_PROMISE"]


def test_define_rubric_lists_every_issue() -> None:
    with pytest.raises(RubricError) as caught:
        define_rubric(
            version="v1",
            lenses=["a", "a"],
            error_classes=[
                ErrorClassDef(code="X", label="x", lenses=("a",), include="i", exclude="e"),
                ErrorClassDef(code="X", label="x", lenses=("b",), include="i", exclude="e"),
            ],
        )
    issues = caught.value.issues
    assert 'lens "a" is declared twice' in issues
    assert 'error class code "X" is not unique' in issues
    assert any('undeclared lens "b"' in issue for issue in issues)


def test_parse_rubric_returns_a_result() -> None:
    bad = parse_rubric({"version": "", "lenses": [], "errorClasses": {}})
    assert bad == Err(
        [
            "version must be a non-empty string",
            "lenses must be a non-empty array",
            "errorClasses must be an array",
        ]
    )
    good = parse_rubric({"version": "v2", "lenses": ["x"], "errorClasses": []})
    assert isinstance(good, Ok)
    assert good.value.lenses == ("x",)
    assert good.ok is True
    assert bad.ok is False


def test_rubric_round_trips_through_json() -> None:
    parsed = parse_rubric(rubric.to_dict())
    assert isinstance(parsed, Ok)
    assert parsed.value == rubric


def test_fail_requires_class_severity_and_note(clock: Clock) -> None:
    raw = {**clock.review("i1", "accuracy", "PASS").to_dict(), "verdict": "FAIL"}
    result = validate_review(raw)
    assert isinstance(result, Err)
    assert result.error == [
        "a FAIL requires errorClass",
        "a FAIL requires severity",
        "a FAIL requires a note",
    ]
    assert isinstance(validate_review(raw, require_fail_details=False), Ok)


def test_lens_and_class_are_checked_against_the_same_rubric_version(clock: Clock) -> None:
    wrong = validate_review(
        clock.review("i1", "tone", "FAIL", error_class="WRONG_FACT").to_dict(), rubric=rubric
    )
    assert isinstance(wrong, Err)
    assert 'not available to lens "tone"' in wrong.error[0]
    unknown = validate_review(clock.review("i1", "speed", "PASS").to_dict(), rubric=rubric)
    assert isinstance(unknown, Err)
    older = validate_review(
        clock.review("i1", "speed", "PASS", rubric_version="v0").to_dict(), rubric=rubric
    )
    assert isinstance(older, Ok)


def test_malformed_verdict_timestamp_and_seconds(clock: Clock) -> None:
    raw = {
        **clock.review("i1", "tone", "PASS").to_dict(),
        "verdict": "MAYBE",
        "createdAt": "yesterday",
        "seconds": -1,
    }
    result = validate_review(raw)
    assert isinstance(result, Err)
    assert len(result.error) == 3


def test_review_round_trips_camel_case(clock: Clock) -> None:
    review = clock.review("i1", "tone", "FAIL", seconds=12)
    data = review.to_dict()
    assert data["itemId"] == "i1"
    assert data["errorClass"] == "DISMISSIVE"
    assert "tenant" not in data
    parsed = validate_review(data)
    assert isinstance(parsed, Ok)
    assert parsed.value == review


def test_parse_item_requires_id_and_output() -> None:
    assert isinstance(parse_item({"id": "a", "output": None}), Ok)
    assert parse_item({"id": "", "meta": 3}) == Err(
        ["id must be a non-empty string", "output is required", "meta must be an object"]
    )


def test_normalize_seconds() -> None:
    assert normalize_seconds(41.6) == 42
    assert normalize_seconds("90") == 90
    assert normalize_seconds(0) is None
    assert normalize_seconds(3600) is None
    assert normalize_seconds("abc") is None
    assert normalize_seconds(None) is None


def test_latest_per_lens_ignores_lenses_outside_the_rubric(clock: Clock) -> None:
    rows = [
        clock.review("i1", "accuracy", "FAIL"),
        clock.review("i1", "accuracy", "PASS"),
        clock.review("i1", "speed", "FAIL"),
        clock.review("i1", "tone", "PASS"),
    ]
    current = latest_per_lens(rows, rubric.lenses)
    assert [(r.lens, r.verdict) for r in current] == [("accuracy", "PASS"), ("tone", "PASS")]


def test_latest_per_lens_honours_created_at_then_append_order(clock: Clock) -> None:
    later = clock.review("i1", "accuracy", "PASS", created_at="2026-10-02T00:00:00.000Z")
    earlier = clock.review("i1", "accuracy", "FAIL", created_at="2026-10-01T00:00:00.000Z")
    assert latest_per_lens([later, earlier], ["accuracy"])[0].verdict == "PASS"
    a = clock.review("i1", "accuracy", "PASS", created_at="2026-10-01T00:00:00.000Z")
    b = clock.review("i1", "accuracy", "NEEDS_REVIEW", created_at="2026-10-01T00:00:00Z")
    assert latest_per_lens([a, b], ["accuracy"])[0].verdict == "NEEDS_REVIEW"


def test_missing_lenses_in_rubric_order(clock: Clock) -> None:
    assert missing_lenses([clock.review("i1", "tone", "PASS")], rubric.lenses) == [
        "accuracy",
        "policy",
    ]


def test_complete_items_scoped_to_one_version(clock: Clock) -> None:
    rows = [
        *clock.triple("i1", "PASS", "PASS", "PASS"),
        clock.review("i2", "accuracy", "PASS"),
        clock.review("i2", "tone", "PASS"),
        *clock.triple("i3", "PASS", "FAIL", "PASS", rubric_version="v0"),
    ]
    assert [item.item_id for item in complete_items(rows, rubric.lenses)] == ["i1", "i3"]
    scoped = complete_items(rows, rubric.lenses, rubric_version="v1")
    assert [item.item_id for item in scoped] == ["i1"]
    assert incomplete_items(rows, rubric.lenses) == [
        IncompleteItem(item_id="i2", present=["accuracy", "tone"], missing=["policy"])
    ]
