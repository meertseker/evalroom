"""CLI commands for Inspect logs: ``gate <log>``, ``compare <a> <b>`` and ``judge-check``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, cast

from .._js import to_fixed
from ..cli.format import Colors, table
from ..cli.main import (
    EXIT,
    USAGE,
    CliIO,
    DataError,
    Parsed,
    UsageError,
    decision_label,
    emit_comparison,
    emit_json,
    gate_exit,
    need,
    num,
    parse,
    render_gate,
    resolve,
)
from ..stats import format_interval, percent
from ._values import PARTIAL_POLICIES, Partial
from .judge import DEFAULT_MIN_AGREEMENT, JudgeCheckReport, JudgeDisagreement, judge_check
from .logs import InspectLogError, ScorerNotFound, compare_logs, gate_log

__all__ = ["compare_logs_command", "gate_log_command", "judge_check_command", "render_judge_check"]


def _partial(parsed: Parsed) -> Partial:
    value = parsed.get("--partial") or "fail"
    if value not in PARTIAL_POLICIES:
        raise UsageError(f'--partial must be one of {", ".join(PARTIAL_POLICIES)}, got "{value}"')
    return cast(Partial, value)


def gate_log_command(
    io: CliIO,
    c: Colors,
    path: str,
    parsed: Parsed,
    *,
    min_pass_rate: float,
    min_n: float,
    max_width: float,
) -> int:
    """``evalroom gate <log.eval>``."""
    try:
        result = gate_log(
            resolve(io, path),
            scorer=parsed.get("--scorer"),
            min_pass_rate=min_pass_rate,
            min_n=min_n,
            max_width=max_width,
            partial=_partial(parsed),
        )
    except ScorerNotFound as error:
        raise UsageError(str(error)) from error
    except InspectLogError as error:
        raise DataError(str(error)) from error
    if parsed.get("--json"):
        emit_json(io, result.to_dict())
    else:
        text = render_gate(result.run.label, path, f"scorer {result.scorer}", result.gate, c)
        text = text.replace(" · rubric scorer ", " · scorer ")
        notes = []
        if result.reviewed_edits:
            notes.append(
                f"{result.reviewed_edits} scores were corrected by a human; "
                "the gate uses the corrections."
            )
        if result.excluded:
            notes.append(
                f"{result.excluded} samples left out (unscored or partial credit excluded)."
            )
        io.stdout(text + "".join(f"{c.dim(note)}\n" for note in notes))
    return gate_exit(result.gate)


def compare_logs_command(io: CliIO, c: Colors, base: str, candidate: str, parsed: Parsed) -> int:
    """``evalroom compare <a.eval> <b.eval>``."""
    try:
        result = compare_logs(
            resolve(io, base),
            resolve(io, candidate),
            scorer=parsed.get("--scorer"),
            partial=_partial(parsed),
        )
    except ScorerNotFound as error:
        raise UsageError(str(error)) from error
    except InspectLogError as error:
        raise DataError(str(error)) from error
    return emit_comparison(
        io, c, result.base, result.candidate, result.comparison, bool(parsed.get("--json"))
    )


def judge_check_command(args: Sequence[str], io: CliIO, c: Colors) -> int:
    """``evalroom judge-check <log.eval>``."""
    parsed = parse(
        args,
        ("--scorer", "string"),
        ("--human", "string"),
        ("--partial", "string"),
        ("--assume-unedited-agree", "boolean"),
        ("--min-agreement", "string"),
        ("--min-n", "string"),
        ("--max-width", "string"),
        ("--top", "string"),
    )
    if parsed.get("--help"):
        io.stdout(USAGE)
        return EXIT.ok
    path = need(parsed.positionals[0] if parsed.positionals else None, "<log.eval>")
    min_agreement = num(parsed.get("--min-agreement"), "--min-agreement", DEFAULT_MIN_AGREEMENT)
    if not 0 <= min_agreement <= 1:
        raise UsageError("--min-agreement must be between 0 and 1")
    top = num(parsed.get("--top"), "--top", 10)
    if top < 0 or not float(top).is_integer():
        raise UsageError("--top must be a whole number")
    human = parsed.get("--human")
    try:
        report = judge_check(
            resolve(io, path),
            scorer=parsed.get("--scorer"),
            human=resolve(io, human) if human is not None else None,
            partial=_partial(parsed),
            assume_unedited_agree=bool(parsed.get("--assume-unedited-agree")),
            min_agreement=min_agreement,
            min_n=num(parsed.get("--min-n"), "--min-n", 50),
            max_width=num(parsed.get("--max-width"), "--max-width", 0.25),
            top=int(top),
        )
    except ScorerNotFound as error:
        raise UsageError(str(error)) from error
    except InspectLogError as error:
        raise DataError(str(error)) from error
    if parsed.get("--json"):
        emit_json(io, report.to_dict())
    else:
        io.stdout(render_judge_check(report, c, path))
    return report.exit_code


def _decision(report: JudgeCheckReport, c: Colors) -> str:
    if report.decision == "acceptable":
        return c.green("ACCEPTABLE")
    if report.decision == "not_acceptable":
        return c.red("NOT ACCEPTABLE")
    return decision_label("undecidable", c)


def render_judge_check(report: JudgeCheckReport, c: Colors, path: str | None = None) -> str:
    """Renders a judge check for the terminal."""
    judge = f" (judge {report.judge})" if report.judge else ""
    source = (
        f"labels from {report.labels_file}"
        if report.source == "labels"
        else "a human edit in Score.history"
    )
    counts = [
        f"{report.samples} scored samples",
        f"{report.reviewed} reviewed ({source})",
        f"{report.not_reviewed} not reviewed, excluded",
    ]
    if report.excluded:
        counts.append(f"{report.excluded} reviewed but excluded")
    out = [
        f"{c.bold('evalroom judge-check')} · {c.cyan(report.task)} · scorer {report.scorer}{judge}",
        c.dim(" · ".join(counts)),
    ]
    if report.assumed:
        out.append(
            c.yellow(
                f"--assume-unedited-agree: {report.assumed} unreviewed samples counted as "
                "agreements. That assumes reviewers looked at them; the numbers below are "
                "optimistic if they did not."
            )
        )
    if report.unmatched_labels:
        out.append(c.yellow(f"{report.unmatched_labels} labels match no sample in the log."))
    kappa = report.kappa
    kappa_text = (
        c.dim(f"n/a ({kappa.reason})")
        if kappa.value is None
        else f"{to_fixed(kappa.value, 2)}  {kappa.band}"
    )
    out += [
        "",
        table(
            [
                ["Agreement", format_interval(report.agreement)],
                ["Judge wrong", format_interval(report.judge_wrong)],
                ["Cohen's kappa", kappa_text],
                ["Bar", f"{percent(report.min_agreement)} agreement on the lower bound"],
                ["Decision", _decision(report, c)],
                ["Reason", report.reason],
            ]
        ),
        "",
    ]
    if report.labels:
        rows = [
            [judge_label, *[str(report.confusion[judge_label][h]) for h in report.labels]]
            for judge_label in report.labels
        ]
        aligns: list[Literal["left", "right"]] = ["left"]
        aligns += ["right"] * len(report.labels)
        out.append(
            table(
                rows,
                header=["Judge \\ Human", *report.labels],
                align=aligns,
            )
        )
        out.append("")
    if report.disagreements:
        shown = len(report.disagreements)
        out.append(c.bold(f"Disagreements ({report.total_disagreements}, showing {shown})"))
        width = max(len(_sample(d)) for d in report.disagreements)
        pad = " " * (width + 4)
        for d in report.disagreements:
            who = f" ({d.reviewer})" if d.reviewer else ""
            out.append(
                f"  {c.bold(_sample(d).ljust(width))}  judge {c.red(d.judge)}, human "
                f"{c.green(d.human)} · {_cut(d.input, 60)} → {_cut(d.target, 30)}"
            )
            out.append(f"{pad}{c.dim('judge:')} {_cut(d.judge_explanation, 84)}")
            if d.human_reason:
                out.append(f"{pad}{c.dim('human' + who + ':')} {_cut(d.human_reason, 84)}")
        out.append("")
    if report.warning:
        out.append(c.yellow(f"Warning: {report.warning}."))
    elif report.decision == "not_acceptable":
        out.append(c.red("The judge disagrees with reviewers too often to grade this task alone."))
    return "\n".join(out).rstrip("\n") + "\n"


def _sample(d: JudgeDisagreement) -> str:
    return str(d.sample_id) + (f"#{d.epoch}" if d.epoch != 1 else "")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
