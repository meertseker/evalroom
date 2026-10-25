"""Small, dependency-free statistics.

Every rate this module produces carries its interval and its n: there is no function here
that returns a bare percentage.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ._js import math_round
from ._serde import to_json

__all__ = [
    "DEFAULT_MAX_WIDTH",
    "DEFAULT_MIN_N",
    "DEFAULT_Z",
    "Interval",
    "KappaBand",
    "cohen_kappa",
    "fleiss_kappa",
    "format_interval",
    "format_rate",
    "interpret_kappa",
    "median",
    "percent",
    "wilson",
]

DEFAULT_Z = 1.96
"""Default z for a two-sided 95% interval."""
DEFAULT_MIN_N = 50
"""Below this many trials a rate never decides a gate."""
DEFAULT_MAX_WIDTH = 0.25
"""A 95% interval wider than this (high - low) is too loose to act on."""


@dataclass(frozen=True, slots=True)
class Interval:
    """A proportion with its Wilson score interval, its sample size and a decidability flag."""

    p: float
    """Point estimate ``k / n`` in [0, 1]; ``0`` when ``n`` is 0."""
    low: float
    """Lower bound, clamped to [0, 1]."""
    high: float
    """Upper bound, clamped to [0, 1]."""
    k: float
    """Number of successes the estimate is built from."""
    n: float
    """Number of trials the estimate is built from."""
    decidable: bool
    """``True`` only when ``n >= min_n`` and ``high - low <= max_width``. A rate that is not
    decidable may be printed, but it must not decide a gate."""

    @property
    def width(self) -> float:
        """``high - low``."""
        return self.high - self.low

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape: ``{"p", "low", "high", "k", "n", "decidable"}``."""
        result: dict[str, Any] = to_json(self)
        return result

    def __str__(self) -> str:
        return format_interval(self)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def wilson(
    passed: float,
    total: float,
    *,
    z: float = DEFAULT_Z,
    min_n: float = DEFAULT_MIN_N,
    max_width: float = DEFAULT_MAX_WIDTH,
) -> Interval:
    """Wilson score interval for a binomial proportion.

    ``passed`` is clamped to ``[0, total]``. A non-finite or non-positive ``total`` returns an
    empty, undecidable interval (``n = 0``).

    >>> wilson(12, 20).low
    0.3865...
    """
    if not _finite(total) or total <= 0 or not _finite(passed):
        return Interval(p=0, low=0, high=0, k=0, n=0, decidable=False)
    k = min(max(passed, 0), total)
    p = k / total
    z2 = z * z
    denom = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denom
    half = (z * math.sqrt((p * (1 - p)) / total + z2 / (4 * total * total))) / denom
    low = max(0.0, center - half)
    high = min(1.0, center + half)
    return Interval(
        p=p,
        low=low,
        high=high,
        k=k,
        n=total,
        decidable=total >= min_n and high - low <= max_width,
    )


def percent(value: float) -> str:
    """Rounds a proportion to a whole percentage string, e.g. ``0.604 -> "60%"``."""
    return f"{math_round(value * 100)}%"


def _count(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def format_interval(interval: Interval) -> str:
    """Formats an interval so the rate, the counts and the interval always travel together.

    ``"60% (12/20) · 39%–78% · not decidable"``
    """
    if interval.n == 0:
        return "no data · not decidable"
    base = (
        f"{percent(interval.p)} ({_count(interval.k)}/{_count(interval.n)}) · "
        f"{percent(interval.low)}–{percent(interval.high)}"
    )
    return base if interval.decidable else f"{base} · not decidable"


def format_rate(
    passed: float,
    total: float,
    *,
    z: float = DEFAULT_Z,
    min_n: float = DEFAULT_MIN_N,
    max_width: float = DEFAULT_MAX_WIDTH,
) -> str:
    """:func:`wilson` for ``passed / total``, formatted with :func:`format_interval`.

    >>> format_rate(12, 20)
    '60% (12/20) · 39%–78% · not decidable'
    """
    return format_interval(wilson(passed, total, z=z, min_n=min_n, max_width=max_width))


def median(values: Iterable[float]) -> float | None:
    """Median of the finite values; ``None`` when there are none. Does not mutate the input."""
    ordered = sorted(v for v in values if _finite(v))
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _total(values: Iterable[float | None]) -> float:
    return sum((v or 0) for v in values)


def fleiss_kappa(rows: Sequence[Mapping[str, float | None]]) -> float | None:
    """Fleiss' kappa for a fixed number of raters per subject.

    Each row maps a category to the number of raters who chose it for one subject. The rater
    count is taken from the first row; rows with a different total are ignored. Returns
    ``None`` when there are no usable rows, fewer than two raters, or when chance agreement is
    total (every rating in one category), where kappa is undefined.
    """
    if not rows:
        return None
    raters = _total(rows[0].values())
    if raters < 2:
        return None
    usable = [row for row in rows if _total(row.values()) == raters]
    if not usable:
        return None
    categories: list[str] = []
    for row in usable:
        for category in row:
            if category not in categories:
                categories.append(category)
    subjects = len(usable)
    p_bar = (
        _total(
            (_total((c or 0) * (c or 0) for c in row.values()) - raters) / (raters * (raters - 1))
            for row in usable
        )
        / subjects
    )
    p_e = 0.0
    for category in categories:
        pj = _total(row.get(category) or 0 for row in usable) / (subjects * raters)
        p_e += pj * pj
    if 1 - p_e <= 1e-12:
        return None
    return (p_bar - p_e) / (1 - p_e)


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Cohen's kappa for two raters who labelled the same subjects in the same order.

    Returns ``None`` for empty input or when expected agreement is total (both raters used a
    single, identical category), where kappa is undefined.

    :raises ValueError: when ``a`` and ``b`` have different lengths.
    """
    if len(a) != len(b):
        raise ValueError(f"cohen_kappa: rater label arrays differ in length ({len(a)} vs {len(b)})")
    n = len(a)
    if n == 0:
        return None
    counts_a: dict[str, int] = {}
    counts_b: dict[str, int] = {}
    agreed = 0
    for x, y in zip(a, b, strict=True):
        if x == y:
            agreed += 1
        counts_a[x] = counts_a.get(x, 0) + 1
        counts_b[y] = counts_b.get(y, 0) + 1
    p_o = agreed / n
    p_e = 0.0
    for category, count in counts_a.items():
        p_e += (count / n) * (counts_b.get(category, 0) / n)
    if 1 - p_e <= 1e-12:
        return None
    return (p_o - p_e) / (1 - p_e)


KappaBand = Literal["poor", "slight", "fair", "moderate", "substantial", "almost perfect"]
"""Landis & Koch (1977) agreement bands."""


def interpret_kappa(kappa: float | None) -> KappaBand | None:
    """Maps a kappa to its Landis & Koch (1977) band.

    ``< 0`` poor, ``0–0.20`` slight, ``0.21–0.40`` fair, ``0.41–0.60`` moderate,
    ``0.61–0.80`` substantial, ``0.81–1`` almost perfect. ``None`` for ``None`` or
    non-finite input.
    """
    if kappa is None or not _finite(kappa):
        return None
    if kappa < 0:
        return "poor"
    if kappa <= 0.2:
        return "slight"
    if kappa <= 0.4:
        return "fair"
    if kappa <= 0.6:
        return "moderate"
    if kappa <= 0.8:
        return "substantial"
    return "almost perfect"
