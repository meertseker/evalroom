"""``judge_check``: how often an LLM judge agrees with the humans who corrected it.

The human truth comes from the scores people already corrected in Inspect (``edit_score`` or
the log viewer, recorded in ``Score.history`` with provenance), or from a labels file. The
judge's label is the scorer's original value; the human label is the edited value. A human
edit that keeps the value is a confirmation. Samples nobody reviewed are excluded.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from inspect_ai.log import EvalLog

from .._serde import optional, to_json
from ..agreement import DEFAULT_MIN_KAPPA_ITEMS, KappaStat, kappa_stat
from ..evalrun import decide
from ..stats import DEFAULT_MAX_WIDTH, DEFAULT_MIN_N, Interval, cohen_kappa, wilson
from ._values import PARTIAL_POLICIES, Partial, UnreadableValue, excerpt, label_of
from .logs import InspectLogError, LogLike, load_log, resolve_scorer, scored_samples

__all__ = [
    "DEFAULT_MIN_AGREEMENT",
    "UNDECIDABLE_WARNING",
    "HumanLabel",
    "JudgeCheckReport",
    "JudgeDecision",
    "JudgeDisagreement",
    "judge_check",
    "load_human_labels",
]

DEFAULT_MIN_AGREEMENT = 0.8
"""The judge is acceptable when the agreement interval's lower bound is at least this."""

UNDECIDABLE_WARNING = "do not trust this judge on this evidence yet"
"""Printed whenever the decision is undecidable."""

JudgeDecision = Literal["acceptable", "not_acceptable", "undecidable"]


@dataclass(frozen=True, slots=True)
class HumanLabel:
    """One human label from a labels file."""

    sample_id: str
    """The sample id, as text."""
    label: Any
    """The grade (``C``, ``I``, ``P``, a boolean or 0/1)."""
    epoch: int | None = None
    """The epoch, when the log has several."""
    reason: str | None = None
    """Why, if the file gives a reason."""
    reviewer: str | None = None
    """Who, if the file says."""


def _human_from(row: Mapping[str, Any], where: str) -> HumanLabel:
    sample_id = row.get("sampleId", row.get("sample_id", row.get("id")))
    if sample_id is None or str(sample_id).strip() == "":
        raise InspectLogError(f"{where}: needs a sampleId")
    if "label" not in row or row["label"] is None or str(row["label"]).strip() == "":
        raise InspectLogError(f"{where}: needs a label")
    epoch_raw = row.get("epoch")
    epoch: int | None = None
    if epoch_raw is not None and str(epoch_raw).strip() != "":
        try:
            epoch = int(str(epoch_raw))
        except ValueError as error:
            raise InspectLogError(f"{where}: epoch must be an integer") from error
    reason = row.get("reason")
    reviewer = row.get("reviewer")
    return HumanLabel(
        sample_id=str(sample_id).strip(),
        label=row["label"],
        epoch=epoch,
        reason=str(reason) if reason not in (None, "") else None,
        reviewer=str(reviewer) if reviewer not in (None, "") else None,
    )


def load_human_labels(path: str | Path) -> list[HumanLabel]:
    """Reads human labels from JSONL (``{"sampleId", "epoch"?, "label", "reason"?}``) or CSV
    with the same columns (header row required).

    :raises InspectLogError: on a missing file or a malformed row.
    """
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise InspectLogError(f"cannot read {path}: {error.strerror or error}") from error
    labels: list[HumanLabel] = []
    if file.suffix.lower() == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "label" not in reader.fieldnames:
            raise InspectLogError(f"{path}: a CSV needs a header with sampleId and label columns")
        for number, row in enumerate(reader, start=2):
            labels.append(_human_from(row, f"{path}:{number}"))
        return labels
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as error:
            raise InspectLogError(f"{path}:{number}: {error}") from error
        if not isinstance(row, dict):
            raise InspectLogError(f"{path}:{number}: each line must be a JSON object")
        labels.append(_human_from(row, f"{path}:{number}"))
    return labels


@dataclass(frozen=True, slots=True)
class JudgeDisagreement:
    """A reviewed sample where the judge and the human disagree."""

    sample_id: str | int
    epoch: int
    judge: str
    """The judge's label."""
    human: str
    """The human label."""
    input: str
    """Input excerpt."""
    target: str
    """Target excerpt."""
    judge_explanation: str
    """Excerpt of the judge's reasoning."""
    human_reason: str | None = optional()
    """Why the human changed (or kept) the grade."""
    reviewer: str | None = optional()
    """Who reviewed it."""


@dataclass(frozen=True, slots=True)
class JudgeCheckReport:
    """Output of :func:`judge_check`. ``to_dict()`` is the ``--json`` shape."""

    log: str | None
    """Where the log was read from."""
    task: str
    """Inspect task name."""
    model: str
    """Model under test."""
    judge: str | None
    """The judge model, when the scorer's options name one."""
    scorer: str
    """The scorer whose labels were checked."""
    source: Literal["edits", "labels"]
    """Human truth from ``Score.history`` edits, or from a labels file."""
    partial: Partial
    """How partial credit was counted."""
    samples: int
    """Samples with a score from this scorer."""
    reviewed: int
    """Samples with a judge label and a human label (n of every statistic below)."""
    not_reviewed: int
    """Samples nobody reviewed; excluded unless ``assume_unedited_agree``."""
    assumed: int
    """Unreviewed samples counted as agreements (``--assume-unedited-agree``); 0 by default."""
    excluded: int
    """Reviewed samples left out: unscored, created by an edit, or partial credit under
    ``partial="exclude"``."""
    unmatched_labels: int
    """Rows of a labels file that match no sample."""
    agreement: Interval
    """Share of reviewed samples where the judge's label equals the human label."""
    judge_wrong: Interval
    """Share of reviewed samples where the judge was wrong (1 - agreement)."""
    kappa: KappaStat
    """Cohen's kappa between judge and human labels."""
    labels: list[str]
    """Labels observed, judge or human, in order."""
    confusion: dict[str, dict[str, int]]
    """``confusion[judge_label][human_label]`` = number of samples."""
    disagreements: list[JudgeDisagreement]
    """The first ``top`` disagreements, in log order."""
    total_disagreements: int
    """All disagreements, including those not listed."""
    decision: JudgeDecision
    """``acceptable``, ``not_acceptable`` or ``undecidable``."""
    reason: str
    """One sentence explaining the decision."""
    warning: str | None
    """``"do not trust this judge on this evidence yet"`` when undecidable."""
    min_agreement: float
    min_n: float
    max_width: float
    labels_file: str | None = field(default=None)
    """The labels file, when one was used."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape printed by ``evalroom judge-check --json``."""
        result: dict[str, Any] = to_json(self)
        return result

    @property
    def exit_code(self) -> int:
        """0 acceptable, 1 not acceptable, 2 undecidable."""
        return {"acceptable": 0, "not_acceptable": 1, "undecidable": 2}[self.decision]


def _judge_model(log: EvalLog, scorer: str) -> str | None:
    for spec in log.eval.scorers or []:
        if spec.name != scorer or not spec.options:
            continue
        model = spec.options.get("model")
        if isinstance(model, str):
            return model
        if isinstance(model, dict) and isinstance(model.get("model"), str):
            return str(model["model"])
        if isinstance(model, list) and model:
            first = model[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict) and isinstance(first.get("model"), str):
                return str(first["model"])
    roles = getattr(log.eval, "model_roles", None) or {}
    grader = roles.get("grader") if isinstance(roles, dict) else None
    name = getattr(grader, "model", None)
    return name if isinstance(name, str) else None


def judge_check(
    log: LogLike,
    *,
    scorer: str | None = None,
    human: str | Path | Sequence[HumanLabel] | None = None,
    partial: Partial = "fail",
    assume_unedited_agree: bool = False,
    min_agreement: float = DEFAULT_MIN_AGREEMENT,
    min_n: float = DEFAULT_MIN_N,
    max_width: float = DEFAULT_MAX_WIDTH,
    top: int = 10,
    min_kappa_items: int = DEFAULT_MIN_KAPPA_ITEMS,
) -> JudgeCheckReport:
    """Measures how often the judge's grades match the humans' on reviewed samples.

    - Human truth: by default, every score with a human edit in ``Score.history`` (an entry
      with provenance). The judge label is the original value, the human label the current
      value; an edit that keeps the value is a confirmation. With ``human`` (a JSONL/CSV path
      or a list of :class:`HumanLabel`), the file replaces the edits and defines the reviewed
      set.
    - ``scorer`` defaults to the log's only scorer, or ``model_graded_qa`` if present.
    - Decision, with the core gate's rule: **undecidable** when ``reviewed < min_n`` or the
      agreement interval is wider than ``max_width`` (or straddles the bar); **acceptable**
      when its lower bound is at least ``min_agreement``; otherwise **not acceptable**.
    """
    if partial not in PARTIAL_POLICIES:
        raise ValueError(f"partial must be one of {', '.join(PARTIAL_POLICIES)}")
    loaded = load_log(log)
    name = resolve_scorer(loaded, scorer, prefer="model_graded_qa")
    labels_file: str | None = None
    human_labels: list[HumanLabel] | None = None
    if isinstance(human, (str, Path)):
        labels_file = str(human)
        human_labels = load_human_labels(human)
    elif human is not None:
        human_labels = list(human)

    samples = list(scored_samples(loaded, name))
    epochs: dict[str, set[int]] = {}
    for sample in samples:
        epochs.setdefault(str(sample.sample_id), set()).add(sample.epoch)

    by_key: dict[tuple[str, int], HumanLabel] = {}
    unmatched = 0
    if human_labels is not None:
        for label in human_labels:
            known = epochs.get(label.sample_id)
            if not known:
                unmatched += 1
                continue
            epoch = label.epoch
            if epoch is None:
                if len(known) > 1:
                    raise InspectLogError(
                        f"sample {label.sample_id} has {len(known)} epochs; give an epoch"
                    )
                epoch = next(iter(known))
            if epoch not in known:
                unmatched += 1
                continue
            by_key[(label.sample_id, epoch)] = label

    pairs: list[tuple[str, str]] = []
    rows: list[JudgeDisagreement] = []
    total_disagreements = 0
    not_reviewed = 0
    assumed = 0
    excluded = 0
    for sample in samples:
        key = (str(sample.sample_id), sample.epoch)
        file_label = by_key.get(key)
        if human_labels is not None:
            reviewed = file_label is not None
            human_value = file_label.label if file_label is not None else None
            reason = file_label.reason if file_label is not None else None
            reviewer = file_label.reviewer if file_label is not None else None
        else:
            reviewed = sample.edited
            human_value = sample.current
            reason = sample.edit_reason
            reviewer = sample.editor
        if sample.original is None:
            excluded += 1 if reviewed else 0
            continue
        try:
            judge_label = label_of(sample.original, partial)
            human_label = label_of(human_value, partial) if reviewed else None
        except UnreadableValue as error:
            raise InspectLogError(f"sample {sample.sample_id}: {error}") from error
        if not reviewed:
            if assume_unedited_agree and judge_label is not None:
                assumed += 1
                pairs.append((judge_label, judge_label))
            else:
                not_reviewed += 1
            continue
        if judge_label is None or human_label is None:
            excluded += 1
            continue
        pairs.append((judge_label, human_label))
        if judge_label != human_label:
            total_disagreements += 1
            if len(rows) < top:
                rows.append(
                    JudgeDisagreement(
                        sample_id=sample.sample_id,
                        epoch=sample.epoch,
                        judge=judge_label,
                        human=human_label,
                        input=excerpt(sample.input, 80),
                        target=excerpt(sample.target, 60),
                        judge_explanation=excerpt(sample.explanation, 120),
                        human_reason=reason,
                        reviewer=reviewer,
                    )
                )

    n = len(pairs)
    agreed = sum(1 for judge_label, human_label in pairs if judge_label == human_label)
    agreement = wilson(agreed, n, min_n=min_n, max_width=max_width)
    judge_wrong = wilson(n - agreed, n, min_n=min_n, max_width=max_width)
    order = ["C", "P", "I", "N"]
    seen = {label for pair in pairs for label in pair}
    labels = [label for label in order if label in seen] + sorted(seen - set(order))
    confusion = {row: dict.fromkeys(labels, 0) for row in labels}
    for judge_label, human_label in pairs:
        confusion[judge_label][human_label] += 1
    kappa = kappa_stat(
        cohen_kappa([p[0] for p in pairs], [p[1] for p in pairs]) if n >= min_kappa_items else None,
        n,
        min_kappa_items,
        "judge and humans used a single identical label; kappa is undefined",
    )
    kappa = KappaStat(
        value=kappa.value,
        band=kappa.band,
        n=kappa.n,
        reason=kappa.reason.replace("complete item", "reviewed sample") if kappa.reason else None,
    )
    gated = decide(
        agreement, min_agreement, min_n=min_n, max_width=max_width, noun="agreement", unit="labels"
    )
    decision: JudgeDecision = {
        "pass": "acceptable",
        "fail": "not_acceptable",
        "undecidable": "undecidable",
    }[gated.decision]  # type: ignore[assignment]
    return JudgeCheckReport(
        log=loaded.location if isinstance(loaded.location, str) else None,
        task=loaded.eval.task,
        model=loaded.eval.model,
        judge=_judge_model(loaded, name),
        scorer=name,
        source="labels" if human_labels is not None else "edits",
        partial=partial,
        samples=len(samples),
        reviewed=n - assumed,
        not_reviewed=not_reviewed,
        assumed=assumed,
        excluded=excluded,
        unmatched_labels=unmatched,
        agreement=agreement,
        judge_wrong=judge_wrong,
        kappa=kappa,
        labels=labels,
        confusion=confusion,
        disagreements=rows,
        total_disagreements=total_disagreements,
        decision=decision,
        reason=gated.reason,
        warning=UNDECIDABLE_WARNING if decision == "undecidable" else None,
        min_agreement=min_agreement,
        min_n=min_n,
        max_width=max_width,
        labels_file=labels_file,
    )
