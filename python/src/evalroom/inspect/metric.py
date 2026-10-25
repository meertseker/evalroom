"""``wilson_accuracy``: an Inspect metric that never reports a bare accuracy."""

from __future__ import annotations

from inspect_ai.scorer import Metric, SampleScore, Value, metric

from ..stats import DEFAULT_MAX_WIDTH, DEFAULT_MIN_N, DEFAULT_Z, wilson
from ._values import Partial, UnreadableValue, outcome_of

__all__ = ["wilson_accuracy"]


@metric
def wilson_accuracy(
    partial: Partial = "fail",
    z: float = DEFAULT_Z,
    min_n: int = DEFAULT_MIN_N,
    max_width: float = DEFAULT_MAX_WIDTH,
) -> Metric:
    """Accuracy with its Wilson score interval and a decidability flag.

    Returns ``{"wilson_accuracy", "lower", "upper", "decidable", "n"}``. ``decidable`` is ``1.0``
    only when ``n >= min_n`` and ``upper - lower <= max_width``: below that, the accuracy may
    be printed but should not decide anything.

    Values are read with Inspect's conventions (``C`` = 1, ``I`` and ``N`` = 0, numbers 1 and
    0). Partial credit (``P``, or a reduced value strictly between 0 and 1) counts as a fail
    by default; ``partial="pass"`` counts it as a pass and ``partial="exclude"`` leaves it out
    of n. Unscored samples (NaN) are left out; unreadable values count as fails, as they do in
    Inspect's own ``accuracy()``.

    Use it in a task as ``scorer=model_graded_qa()`` with ``metrics=[wilson_accuracy()]``, or
    by name: ``registry_create("metric", "evalroom/wilson_accuracy")`` (``inspect_ai.util``).
    """

    def compute(scores: list[SampleScore]) -> Value:
        passed = 0
        total = 0
        for sample_score in scores:
            try:
                outcome = outcome_of(sample_score.score.value, partial)
            except UnreadableValue:
                outcome = False
            if outcome is None:
                continue
            total += 1
            passed += 1 if outcome else 0
        interval = wilson(passed, total, z=z, min_n=min_n, max_width=max_width)
        return {
            "wilson_accuracy": float(interval.p),
            "lower": float(interval.low),
            "upper": float(interval.high),
            "decidable": 1.0 if interval.decidable else 0.0,
            "n": float(interval.n),
        }

    return compute
