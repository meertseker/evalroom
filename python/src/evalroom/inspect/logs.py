"""Reading Inspect logs: scorer selection, original vs edited scores, gate and compare.

See ``docs/inspect-log-format.md`` for the log structure this module relies on.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from inspect_ai.log import EvalLog, EvalSample, read_eval_log
from inspect_ai.scorer import Score, ScoreEdit

from .._serde import optional, to_json
from ..evalrun import CaseResult, EvalRun, GateResult, RunComparison, compare_runs, gate
from ..rules import RuleFailure
from ..stats import DEFAULT_MAX_WIDTH, DEFAULT_MIN_N
from ._values import Partial, UnreadableValue, excerpt, outcome_of

__all__ = [
    "UNCHANGED",
    "InspectLogError",
    "LogComparison",
    "LogGateResult",
    "ScoredSample",
    "ScorerNotFound",
    "compare_logs",
    "gate_log",
    "load_log",
    "log_to_run",
    "resolve_scorer",
    "scored_samples",
    "scorer_names",
]

UNCHANGED = "UNCHANGED"
"""Inspect's "field not set" marker in a ``ScoreEdit`` (also what nulls read back as)."""


class InspectLogError(ValueError):
    """An Inspect log that cannot be read or does not hold what was asked for."""


class ScorerNotFound(LookupError):
    """The requested scorer is not in the log, or the log has several and none was named."""


LogLike = EvalLog | str | Path


def load_log(log: LogLike) -> EvalLog:
    """An :class:`~inspect_ai.log.EvalLog` from a path (``.eval`` or ``.json``) or a log."""
    if isinstance(log, EvalLog):
        return log
    try:
        loaded = read_eval_log(str(log))
    except FileNotFoundError as error:
        raise InspectLogError(f"cannot read {log}: no such file") from error
    except Exception as error:
        raise InspectLogError(f"cannot read {log} as an Inspect log: {error}") from error
    if not loaded.samples:
        raise InspectLogError(f"{log}: the log has no samples")
    return loaded


def scorer_names(log: EvalLog) -> list[str]:
    """Scorer names in the log: from the results, else from the samples' score keys."""
    names: list[str] = []
    if log.results is not None:
        for score in log.results.scores:
            if score.name not in names:
                names.append(score.name)
    for sample in log.samples or []:
        for name in sample.scores or {}:
            if name not in names:
                names.append(name)
    return names


def resolve_scorer(log: EvalLog, scorer: str | None, prefer: str | None = None) -> str:
    """The scorer to read: ``scorer`` if given, the only one, or ``prefer`` if present.

    :raises ScorerNotFound: when the choice is ambiguous or the scorer is missing.
    """
    names = scorer_names(log)
    if not names:
        raise InspectLogError("the log has no scores")
    if scorer is not None:
        if scorer not in names:
            raise ScorerNotFound(
                f'scorer "{scorer}" is not in the log (scorers: {", ".join(names)})'
            )
        return scorer
    if len(names) == 1:
        return names[0]
    if prefer is not None and prefer in names:
        return prefer
    raise ScorerNotFound(
        f"the log has several scorers; pick one with --scorer ({', '.join(names)})"
    )


def _is_set(value: object) -> bool:
    return value is not None and value != UNCHANGED


@dataclass(frozen=True, slots=True)
class ScoredSample:
    """One sample's score for one scorer, split into the original and the edited value."""

    sample_id: str | int
    """``EvalSample.id``."""
    epoch: int
    """``EvalSample.epoch``."""
    original: Any
    """The scorer's own value (``history[0]`` once edited); ``None`` if the score was
    created by an edit."""
    current: Any
    """The current value (the edited one, when edited)."""
    edited: bool
    """``True`` when at least one history entry carries provenance (a human edit)."""
    explanation: str | None
    """The scorer's own explanation (the judge's reasoning)."""
    answer: str | None
    """The scorer's extracted answer."""
    edit_reason: str | None
    """Reason given with the last edit (provenance reason, else the edit's explanation)."""
    editor: str | None
    """Author of the last edit."""
    input: str
    """The sample input as text."""
    target: str
    """The sample target as text."""


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " | ".join(_text(getattr(item, "text", item)) for item in value)
    return str(value)


def _input_text(sample: EvalSample) -> str:
    raw = sample.input
    if isinstance(raw, str):
        return raw
    for message in reversed(raw):
        if getattr(message, "role", None) == "user":
            text = getattr(message, "text", None)
            if isinstance(text, str):
                return text
    return _text(raw)


def _split(score: Score) -> tuple[Any, bool, str | None, str | None, str | None, str | None]:
    """``(original, edited, explanation, answer, edit_reason, editor)`` for one score."""
    history: list[ScoreEdit] = list(score.history or [])
    edits = [entry for entry in history if entry.provenance is not None]
    if not edits:
        return score.value, False, score.explanation, score.answer, None, None
    first = history[0]
    if first.provenance is None:
        original = first.value if _is_set(first.value) else None
        explanation = first.explanation if _is_set(first.explanation) else None
        answer = first.answer if _is_set(first.answer) else None
    else:
        original, explanation, answer = None, None, None
    last = edits[-1]
    provenance = last.provenance
    reason = provenance.reason if provenance is not None else None
    if not reason and _is_set(last.explanation):
        reason = str(last.explanation)
    editor = provenance.author if provenance is not None else None
    return original, True, explanation, answer, reason, editor


def scored_samples(log: EvalLog, scorer: str) -> Iterator[ScoredSample]:
    """Every sample that has a score for ``scorer``, in log order."""
    for sample in log.samples or []:
        score = (sample.scores or {}).get(scorer)
        if score is None:
            continue
        original, edited, explanation, answer, reason, editor = _split(score)
        yield ScoredSample(
            sample_id=sample.id,
            epoch=sample.epoch,
            original=original,
            current=score.value,
            edited=edited,
            explanation=explanation,
            answer=answer,
            edit_reason=reason,
            editor=editor,
            input=_input_text(sample),
            target=_text(sample.target),
        )


def _multi_epoch(log: EvalLog) -> bool:
    return any(sample.epoch != 1 for sample in log.samples or [])


def case_id(sample_id: str | int, epoch: int, multi_epoch: bool) -> str:
    """Case id of a sample in a run built from a log: ``"id"``, or ``"id#epoch"``."""
    return f"{sample_id}#{epoch}" if multi_epoch else str(sample_id)


def _label(log: EvalLog, path: str | None) -> tuple[str, str]:
    spec = log.eval
    run_id = spec.eval_id or spec.run_id or (Path(path).stem if path else spec.task)
    return run_id, f"{spec.task} · {spec.model}"


def log_to_run(
    log: LogLike,
    *,
    scorer: str | None = None,
    partial: Partial = "fail",
    use: str = "current",
) -> tuple[EvalRun, int]:
    """An :class:`~evalroom.evalrun.EvalRun` with one case per scored sample, and the number
    of samples left out (unscored, or partial credit under ``partial="exclude"``).

    ``use="current"`` reads the edited value where a human corrected the score; ``"original"``
    reads what the scorer said. Case ids are sample ids (``"id#epoch"`` with several epochs).

    :raises InspectLogError: when a value cannot be read as pass/fail.
    """
    loaded = load_log(log)
    name = resolve_scorer(loaded, scorer)
    multi = _multi_epoch(loaded)
    results: list[CaseResult] = []
    excluded = 0
    for sample in scored_samples(loaded, name):
        value = sample.current if use == "current" else sample.original
        try:
            outcome = outcome_of(value, partial)
        except UnreadableValue as error:
            raise InspectLogError(f"sample {sample.sample_id}: {error}") from error
        if outcome is None:
            excluded += 1
            continue
        failures = (
            []
            if outcome
            else [
                RuleFailure(
                    code=f"{name}:{value}", path="", detail=excerpt(sample.explanation, 120)
                )
            ]
        )
        results.append(
            CaseResult(
                case_id=case_id(sample.sample_id, sample.epoch, multi),
                passed=outcome,
                failures=failures,
                output=sample.answer,
            )
        )
    path = loaded.location if isinstance(loaded.location, str) else None
    run_id, label = _label(loaded, path)
    return (
        EvalRun(id=run_id, label=label, rubric_version=f"inspect:{name}", results=results),
        excluded,
    )


@dataclass(frozen=True, slots=True)
class LogGateResult:
    """Output of :func:`gate_log`."""

    log: str | None
    """Where the log was read from."""
    task: str
    model: str
    scorer: str
    run: EvalRun
    """The log as a run (one case per scored sample)."""
    excluded: int
    """Samples left out (unscored, or partial credit under ``partial="exclude"``)."""
    gate: GateResult
    partial: Partial
    reviewed_edits: int = optional(0)
    """How many scores carry a human edit (the gate uses the edited values)."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape printed by ``evalroom gate <log> --json``."""
        return {
            "log": self.log,
            "task": self.task,
            "model": self.model,
            "scorer": self.scorer,
            "partial": self.partial,
            "excluded": self.excluded,
            "humanEdits": self.reviewed_edits,
            "run": {
                "id": self.run.id,
                "label": self.run.label,
                "rubricVersion": self.run.rubric_version,
            },
            **to_json(self.gate),
        }


def gate_log(
    log: LogLike,
    *,
    scorer: str | None = None,
    min_pass_rate: float = 0.9,
    min_n: float = DEFAULT_MIN_N,
    max_width: float = DEFAULT_MAX_WIDTH,
    partial: Partial = "fail",
) -> LogGateResult:
    """Gates an Inspect log on the Wilson interval of its pass rate for one scorer.

    Same decision rule as :func:`evalroom.gate`: undecidable below ``min_n`` or when the
    interval is wider than ``max_width``; pass only when the lower bound clears the bar.
    Human-edited scores count with their edited value.
    """
    loaded = load_log(log)
    name = resolve_scorer(loaded, scorer)
    run, excluded = log_to_run(loaded, scorer=name, partial=partial)
    edits = sum(1 for sample in scored_samples(loaded, name) if sample.edited)
    return LogGateResult(
        log=loaded.location if isinstance(loaded.location, str) else None,
        task=loaded.eval.task,
        model=loaded.eval.model,
        scorer=name,
        run=run,
        excluded=excluded,
        gate=gate(run, min_pass_rate=min_pass_rate, min_n=min_n, max_width=max_width),
        partial=partial,
        reviewed_edits=edits,
    )


@dataclass(frozen=True, slots=True)
class LogComparison:
    """Output of :func:`compare_logs`."""

    scorer: str
    base: EvalRun
    candidate: EvalRun
    comparison: RunComparison
    excluded_base: int
    excluded_candidate: int


def compare_logs(
    base: LogLike,
    candidate: LogLike,
    *,
    scorer: str | None = None,
    partial: Partial = "fail",
) -> LogComparison:
    """Compares two Inspect logs sample by sample (broken / fixed / same) for one scorer.

    Samples are matched by id (and epoch). Without ``scorer``, the logs must share exactly one.
    """
    base_log = load_log(base)
    candidate_log = load_log(candidate)
    if scorer is None:
        shared = [n for n in scorer_names(base_log) if n in scorer_names(candidate_log)]
        if len(shared) != 1:
            listed = ", ".join(shared) or "none"
            raise ScorerNotFound(f"pick a scorer with --scorer (scorers in both logs: {listed})")
        scorer = shared[0]
    base_run, excluded_base = log_to_run(base_log, scorer=scorer, partial=partial)
    candidate_run, excluded_candidate = log_to_run(candidate_log, scorer=scorer, partial=partial)
    if base_run.label == candidate_run.label:
        base_run = _relabel(base_run, base_log, "base")
        candidate_run = _relabel(candidate_run, candidate_log, "candidate")
    return LogComparison(
        scorer=scorer,
        base=base_run,
        candidate=candidate_run,
        comparison=compare_runs(base_run, candidate_run),
        excluded_base=excluded_base,
        excluded_candidate=excluded_candidate,
    )


def _relabel(run: EvalRun, log: EvalLog, fallback: str) -> EvalRun:
    location = log.location if isinstance(log.location, str) else None
    label = Path(location).name if location else fallback
    return EvalRun(id=run.id, label=label, rubric_version=run.rubric_version, results=run.results)
