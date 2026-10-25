from __future__ import annotations

import math
import re
from typing import Any

import pytest

from evalroom.result import Err, Ok
from evalroom.rules import (
    PathRule,
    RuleFailure,
    ScoreResult,
    contains_phrase,
    custom,
    deep_equal,
    get_path,
    normalize_text,
    parse_rules,
    score_output,
)

output: dict[str, Any] = {
    "reply": "Hi Sam, your refund of £24.99 is on its way. It can take 5–7 business days.",
    "actions": ["refund_requested", "tag:billing"],
    "escalate": False,
    "confidence": 0.82,
    "meta": {"lang": "en", "flags": ["a", "b"]},
    "empty": None,
}


def fail_codes(rules: list[PathRule]) -> list[str]:
    return [failure.code for failure in score_output(output, rules).failures]


def test_get_path_resolves_dots_indexes_brackets_and_root() -> None:
    assert get_path(output, "meta.lang").value == "en"
    assert get_path(output, "actions.1").value == "tag:billing"
    assert get_path(output, "meta.flags[1]").value == "b"
    found = get_path(output, "$.escalate")
    assert (found.found, found.value) == (True, False)
    assert get_path(output, "").value is output
    empty = get_path(output, "empty")
    assert (empty.found, empty.value) == (True, None)


def test_get_path_reports_missing_paths() -> None:
    assert get_path(output, "meta.nope.deeper").found is False
    assert get_path(output, "actions.9").found is False
    assert get_path(output, "actions.first").found is False
    assert get_path(output, "reply.length").found is False
    assert get_path({}, "toString").found is False


def test_normalize_text_folds_case_diacritics_punctuation_whitespace() -> None:
    assert normalize_text("  Full\tREFUND,   guaranteed!  ") == "full refund guaranteed"
    assert normalize_text("Café Crème") == "cafe creme"


def test_contains_phrase_matches_whole_phrases_only() -> None:
    assert contains_phrase("Your REFUND is on its way", "refund") is True
    assert contains_phrase("This item is non-refundable", "refund") is False
    assert contains_phrase("We GUARANTEE   it!", "guarantee it") is True


def test_passes_when_every_rule_holds() -> None:
    result = score_output(
        output,
        [
            PathRule("escalate", "eq", False),
            PathRule("meta", "eq", {"lang": "en", "flags": ["a", "b"]}),
            PathRule("meta.lang", "neq", "de"),
            PathRule("meta.lang", "in", ["en", "fr"]),
            PathRule("meta.lang", "notIn", ["de"]),
            PathRule("confidence", "between", [0.5, 0.9]),
            PathRule("reply", "contains", ["refund", "business days"]),
            PathRule("reply", "notContains", ["guarantee", "full card number"]),
            PathRule("actions", "contains", "REFUND_REQUESTED"),
            PathRule("reply", "matches", "^hi ", flags="i"),
            PathRule("reply", "notMatches", r"\d{16}"),
            PathRule("actions", "subsetOf", ["refund_requested", "tag:billing", "escalate"]),
            PathRule("meta.lang", "exists"),
            PathRule("meta.internal", "exists", False),
        ],
    )
    assert result == ScoreResult(passed=True, failures=[])


def test_reports_every_failure() -> None:
    result = score_output(
        output,
        [
            PathRule("escalate", "eq", True, code="SHOULD_ESCALATE"),
            PathRule("reply", "notContains", ["on its way", "refund"], code="FORBIDDEN"),
            PathRule("confidence", "between", [0.9, 1]),
        ],
    )
    assert result.passed is False
    assert result.failures == [
        RuleFailure("SHOULD_ESCALATE", "escalate", "expected true, got false"),
        RuleFailure("FORBIDDEN", "reply", 'contains forbidden "on its way", "refund"'),
        RuleFailure("between(confidence)", "confidence", "0.82 is outside [0.9, 1]"),
    ]


@pytest.mark.parametrize(
    "op", ["eq", "neq", "in", "notIn", "contains", "notContains", "matches", "subsetOf"]
)
def test_missing_path_fails_every_operator_except_exists(op: Any) -> None:
    rule = PathRule("missing.field", op, ["x"])
    assert score_output(output, [rule]).failures[0].detail == "missing.field is missing"


def test_exists_on_a_missing_path() -> None:
    assert score_output(output, [PathRule("missing.field", "exists", False)]).passed
    assert fail_codes([PathRule("missing.field", "exists")]) == ["exists(missing.field)"]


def test_type_mismatches_fail_instead_of_coercing() -> None:
    assert len(fail_codes([PathRule("reply", "between", [0, 1])])) == 1
    assert len(fail_codes([PathRule("confidence", "contains", "8")])) == 1
    assert len(fail_codes([PathRule("confidence", "matches", "0")])) == 1
    assert len(fail_codes([PathRule("reply", "subsetOf", [])])) == 1
    assert len(fail_codes([PathRule("confidence", "eq", "0.82")])) == 1
    assert len(fail_codes([PathRule("escalate", "eq", 0)])) == 1


def test_subset_extras_and_exists_that_should_be_absent() -> None:
    result = score_output(
        output,
        [
            PathRule("actions", "subsetOf", ["refund_requested"]),
            PathRule("escalate", "exists", False),
        ],
    )
    assert [f.detail for f in result.failures] == [
        'not allowed: "tag:billing"',
        "escalate should be absent but is false",
    ]


def test_regex_failure_detail_shows_source_and_flags() -> None:
    result = score_output(output, [PathRule("reply", "matches", "^bye/now", flags="mi")])
    assert result.failures[0].detail == r"does not match /^bye\/now/im"
    compiled = score_output(output, [PathRule("reply", "notMatches", re.compile("refund"))])
    assert compiled.failures[0].detail == "matches forbidden /refund/"


def test_custom_rules_and_thrown_errors() -> None:
    def boom(_: Any) -> bool:
        raise RuntimeError("boom")

    result = score_output(
        output,
        [
            custom("SHORT_ENOUGH", lambda o: len(o["reply"]) < 200),
            custom("HAS_SIGNOFF", lambda o: "Thanks" in o["reply"] or "no sign-off", "reply"),
            custom("BROKEN", boom),
        ],
    )
    assert result.failures == [
        RuleFailure("HAS_SIGNOFF", "reply", "no sign-off"),
        RuleFailure("BROKEN", "", "custom rule threw: boom"),
    ]


def test_structural_equality() -> None:
    assert deep_equal({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]})
    assert not deep_equal({"a": 1}, {"a": 1, "b": None})
    assert not deep_equal([1, 2], [2, 1])
    assert deep_equal(math.nan, math.nan)
    assert deep_equal(1, 1.0)
    assert not deep_equal(True, 1)
    assert not deep_equal(None, False)


def test_long_values_are_truncated_in_details() -> None:
    result = score_output({"a": "x" * 100}, [PathRule("a", "eq", "y")])
    detail = result.failures[0].detail
    assert detail == 'expected "y", got "' + "x" * 76 + "..."


def test_parse_rules_accepts_well_formed_rules() -> None:
    parsed = parse_rules(
        [
            {"path": "reply", "op": "notContains", "value": ["guarantee"], "code": "PROMISE"},
            {"path": "reply", "op": "matches", "value": "^Hi", "flags": "i"},
        ]
    )
    assert isinstance(parsed, Ok)
    assert parsed.value[0].code == "PROMISE"
    assert parsed.value[1].to_dict() == {
        "path": "reply",
        "op": "matches",
        "value": "^Hi",
        "flags": "i",
    }


def test_parse_rules_lists_every_problem() -> None:
    parsed = parse_rules(
        [
            {"path": "a", "op": "startsWith", "value": "x"},
            {"path": "a", "op": "between", "value": [5, 1]},
            {"path": "a", "op": "in", "value": "x"},
            {"path": "a", "op": "matches", "value": "("},
            {"path": "a", "op": "notContains", "value": ["!!"]},
            {"op": "eq"},
        ]
    )
    assert isinstance(parsed, Err)
    assert len(parsed.error) == 6
    assert parsed.error[3].startswith("rules[3]: invalid regex (")
    assert parse_rules("nope") == Err(["rules must be an array"])
