from __future__ import annotations

import math

import pytest

from evalroom._js import number_to_string, stringify, to_fixed
from evalroom.stats import (
    cohen_kappa,
    fleiss_kappa,
    format_interval,
    format_rate,
    interpret_kappa,
    median,
    percent,
    wilson,
)


# Newcombe (1998), "Two-sided confidence intervals for the single proportion",
# Statistics in Medicine 17:857-872, Table I, method 3 (score interval, no continuity correction).
@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [
        (81, 263, 0.2553, 0.3662),
        (15, 148, 0.0624, 0.1605),
        (0, 20, 0, 0.1611),
        (1, 29, 0.0061, 0.1718),
    ],
)
def test_wilson_matches_newcombe_1998(k: int, n: int, low: float, high: float) -> None:
    interval = wilson(k, n)
    assert interval.low == pytest.approx(low, abs=5e-5)
    assert interval.high == pytest.approx(high, abs=5e-5)
    assert interval.k == k
    assert interval.n == n


def test_wilson_hand_computed_12_of_20() -> None:
    # centre = (0.6 + 1.96²/40) / (1 + 1.96²/20) = 0.58389, half-width = 0.19731
    interval = wilson(12, 20)
    assert interval.p == 0.6
    assert interval.low == pytest.approx(0.3866, abs=5e-5)
    assert interval.high == pytest.approx(0.7812, abs=5e-5)


def test_never_decidable_below_min_n() -> None:
    assert wilson(20, 20).decidable is False
    assert wilson(12, 20).decidable is False


def test_decidable_from_min_n_only_when_narrow() -> None:
    assert wilson(40, 50).decidable is True  # width 0.218
    assert wilson(25, 50).decidable is False  # width 0.267 > 0.25


def test_custom_min_n_max_width_and_z() -> None:
    assert wilson(12, 20, min_n=10, max_width=0.5).decidable is True
    assert wilson(40, 50, max_width=0.2).decidable is False
    wider = wilson(40, 50, z=2.576)
    narrower = wilson(40, 50)
    assert wider.width > narrower.width


def test_empty_interval_for_zero_and_non_finite() -> None:
    empty = wilson(0, 0)
    assert (empty.p, empty.low, empty.high, empty.k, empty.n, empty.decidable) == (
        0,
        0,
        0,
        0,
        0,
        False,
    )
    assert wilson(math.nan, 10).n == 0
    assert wilson(1, math.inf).n == 0


def test_clamps_successes_and_bounds() -> None:
    assert wilson(70, 50).k == 50
    assert wilson(-3, 50).k == 0
    every = wilson(50, 50)
    assert every.high == 1
    assert every.low > 0.9
    assert wilson(0, 50).low == 0


def test_format_rate_never_prints_a_bare_rate() -> None:
    assert format_rate(12, 20) == "60% (12/20) · 39%–78% · not decidable"
    assert str(wilson(12, 20)) == "60% (12/20) · 39%–78% · not decidable"


def test_format_rate_drops_the_warning_once_decidable() -> None:
    assert format_rate(80, 100) == "80% (80/100) · 71%–87%"


def test_format_rate_no_data() -> None:
    assert format_rate(0, 0) == "no data · not decidable"
    assert format_interval(wilson(0, 0)) == "no data · not decidable"


def test_percent_rounds_half_up_like_javascript() -> None:
    assert percent(0.125) == "13%"
    assert percent(0.605) == "61%"
    assert percent(0.004) == "0%"


def test_median() -> None:
    values = [3.0, 1.0, 2.0]
    assert median(values) == 2
    assert values == [3.0, 1.0, 2.0]
    assert median([4, 1, 2, 3]) == 2.5
    assert median([]) is None
    assert median([math.nan, 5]) == 5


def test_fleiss_reproduces_wikipedia_worked_example() -> None:
    # https://en.wikipedia.org/wiki/Fleiss%27_kappa#Worked_example: P̄ = 0.378, P_e = 0.213.
    table = [
        [0, 0, 0, 0, 14],
        [0, 2, 6, 4, 2],
        [0, 0, 3, 5, 6],
        [0, 3, 9, 2, 0],
        [2, 2, 8, 1, 1],
        [7, 7, 0, 0, 0],
        [3, 2, 6, 3, 0],
        [2, 5, 3, 2, 2],
        [6, 5, 2, 1, 0],
        [0, 2, 2, 3, 7],
    ]
    rows = [{f"c{j + 1}": float(count) for j, count in enumerate(row)} for row in table]
    assert fleiss_kappa(rows) == pytest.approx(0.20993, abs=5e-6)


def test_fleiss_hand_computed_two_category_table() -> None:
    # P̄ = 0.6, P_e = 0.5 → κ = 0.2
    rows: list[dict[str, float | None]] = [
        {"PASS": 3},
        {"FAIL": 3},
        {"PASS": 2, "FAIL": 1},
        {"PASS": 1, "FAIL": 2},
        {"PASS": 2, "FAIL": 1},
        {"PASS": 1, "FAIL": 2},
        {"PASS": 3},
        {"FAIL": 3},
        {"PASS": 2, "FAIL": 1},
        {"PASS": 1, "FAIL": 2},
    ]
    assert fleiss_kappa(rows) == pytest.approx(0.2, abs=1e-10)


def test_fleiss_perfect_and_systematic_split() -> None:
    assert fleiss_kappa([{"PASS": 3}, {"PASS": 3}, {"FAIL": 3}]) == pytest.approx(1, abs=1e-10)
    split = fleiss_kappa([{"PASS": 1, "FAIL": 1}, {"PASS": 1, "FAIL": 1}])
    assert split == pytest.approx(-1, abs=1e-10)


def test_fleiss_undefined_cases() -> None:
    assert fleiss_kappa([]) is None
    assert fleiss_kappa([{"PASS": 1}]) is None
    assert fleiss_kappa([{"PASS": 3}, {"PASS": 3}]) is None


def test_fleiss_ignores_rows_with_a_different_rater_count() -> None:
    base: list[dict[str, float | None]] = [{"PASS": 3}, {"FAIL": 3}]
    expected = fleiss_kappa(base)
    assert expected is not None
    assert fleiss_kappa([*base, {"PASS": 1}]) == pytest.approx(expected, abs=1e-10)


def test_cohen_reproduces_wikipedia_example() -> None:
    # 50 subjects: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no → p_o = 0.7, p_e = 0.5 → κ = 0.4
    a = ["yes"] * 20 + ["yes"] * 5 + ["no"] * 10 + ["no"] * 15
    b = ["yes"] * 20 + ["no"] * 5 + ["yes"] * 10 + ["no"] * 15
    assert cohen_kappa(a, b) == pytest.approx(0.4, abs=1e-10)


def test_cohen_identical_and_undefined() -> None:
    assert cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == pytest.approx(1, abs=1e-10)
    assert cohen_kappa(["a", "a"], ["a", "a"]) is None
    assert cohen_kappa([], []) is None


def test_cohen_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="differ in length"):
        cohen_kappa(["a"], ["a", "b"])


@pytest.mark.parametrize(
    ("kappa", "band"),
    [
        (-0.01, "poor"),
        (0, "slight"),
        (0.2, "slight"),
        (0.21, "fair"),
        (0.4, "fair"),
        (0.41, "moderate"),
        (0.6, "moderate"),
        (0.61, "substantial"),
        (0.8, "substantial"),
        (0.81, "almost perfect"),
        (1, "almost perfect"),
    ],
)
def test_landis_koch_bands_at_their_boundaries(kappa: float, band: str) -> None:
    assert interpret_kappa(kappa) == band


def test_interpret_kappa_none_and_nan() -> None:
    assert interpret_kappa(None) is None
    assert interpret_kappa(math.nan) is None


def test_javascript_number_formatting() -> None:
    assert number_to_string(1.0) == "1"
    assert number_to_string(0.25) == "0.25"
    assert number_to_string(1e-7) == "1e-7"
    assert number_to_string(0.000001) == "0.000001"
    assert number_to_string(1e21) == "1e+21"
    assert number_to_string(123456789012345680000.0) == "123456789012345680000"
    assert number_to_string(-0.82) == "-0.82"
    assert to_fixed(0.125, 2) == "0.13"
    assert to_fixed(-0.15, 2) == "-0.15"
    assert to_fixed(1.005, 2) == "1.00"  # 1.005 is 1.00499999... in binary
    assert to_fixed(-0.0001, 2) == "-0.00"
    assert stringify({"a": [1.0, 0.5, None, True, "é"]}) == '{"a":[1,0.5,null,true,"é"]}'
    assert stringify({"a": [], "b": {}}, indent=2) == '{\n  "a": [],\n  "b": {}\n}'
