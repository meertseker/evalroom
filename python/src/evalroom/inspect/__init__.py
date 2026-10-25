"""evalroom for Inspect (``pip install "evalroom[inspect]"``).

- :func:`wilson_accuracy`: an Inspect metric with the Wilson interval and a decidability flag
  (also registered as ``evalroom/wilson_accuracy``);
- :func:`judge_check`: how often an LLM judge agrees with the humans who corrected its
  scores in Inspect (``Score.history``) or in a labels file;
- :func:`gate_log` and :func:`compare_logs`: the core gate and comparison on Inspect logs.
"""

from __future__ import annotations

try:
    import inspect_ai  # noqa: F401
except ImportError as error:  # pragma: no cover - exercised by the CLI test with a stub
    raise ImportError(
        'evalroom.inspect needs the Inspect extra: pip install "evalroom[inspect]" '
        "(inspect_ai requires Python 3.11+)"
    ) from error

from ._values import PARTIAL_POLICIES, Partial, label_of, outcome_of
from .judge import (
    DEFAULT_MIN_AGREEMENT,
    UNDECIDABLE_WARNING,
    HumanLabel,
    JudgeCheckReport,
    JudgeDecision,
    JudgeDisagreement,
    judge_check,
    load_human_labels,
)
from .logs import (
    InspectLogError,
    LogComparison,
    LogGateResult,
    ScoredSample,
    ScorerNotFound,
    compare_logs,
    gate_log,
    load_log,
    log_to_run,
    resolve_scorer,
    scored_samples,
    scorer_names,
)
from .metric import wilson_accuracy

__all__ = [
    "DEFAULT_MIN_AGREEMENT",
    "PARTIAL_POLICIES",
    "UNDECIDABLE_WARNING",
    "HumanLabel",
    "InspectLogError",
    "JudgeCheckReport",
    "JudgeDecision",
    "JudgeDisagreement",
    "LogComparison",
    "LogGateResult",
    "Partial",
    "ScoredSample",
    "ScorerNotFound",
    "compare_logs",
    "gate_log",
    "judge_check",
    "label_of",
    "load_human_labels",
    "load_log",
    "log_to_run",
    "outcome_of",
    "resolve_scorer",
    "scored_samples",
    "scorer_names",
    "wilson_accuracy",
]
