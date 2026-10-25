"""The ``evalroom`` command line.

:func:`main` is a function of argv and a :class:`CliIO` so it can be tested in-process;
:func:`evalroom.cli.console_main` wires it to the real process. Commands, flags, ``--json``
output and exit codes match the TypeScript CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

from .._js import math_round, stringify, to_fixed
from .._time import now_iso
from ..agreement import AgreementReport, agreement_report
from ..audit import JsonlAuditSink
from ..evalrun import EvalRun, GateResult, RunComparison, compare_runs, gate, score_run, summarize
from ..golden import GoldenCase, golden_from_dict, promote_and_record, promote_to_golden
from ..jsonl import JsonlLine, JsonlParseError, append_jsonl, read_jsonl_lines
from ..result import Err, Ok, is_record
from ..review import Item, Review, parse_item, validate_review
from ..rubric import VERDICTS, Rubric, parse_rubric
from ..rules import PathRule, parse_rules
from ..stats import format_interval, percent
from ..store.jsonl import read_run_file, write_run_file
from .format import Colors, color_enabled, create_colors, table

__all__ = [
    "CLI_VERSION",
    "EXIT",
    "CliIO",
    "DataError",
    "UsageError",
    "main",
    "render_agreement",
    "render_comparison",
    "render_gate",
]

CLI_VERSION = "0.1.0"
"""Library and CLI version."""


class _Exit:
    ok = 0
    """Success, or gate passed."""
    fail = 1
    """Gate failed, promotion refused, a comparison found broken cases, judge not acceptable."""
    undecidable = 2
    """Gate (or judge check) undecidable."""
    usage = 64
    """Bad command line, or a missing optional extra."""
    data = 65
    """Input file missing or malformed."""


EXIT = _Exit()
"""Exit codes. ``gate`` uses 0/1/2 for pass/fail/undecidable."""


@dataclass(slots=True)
class CliIO:
    """Where the CLI reads its environment and writes its output."""

    stdout: Callable[[str], object]
    """Writes to standard output."""
    stderr: Callable[[str], object]
    """Writes to standard error."""
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    """Environment variables (NO_COLOR / FORCE_COLOR, USER)."""
    is_tty: bool = False
    """Whether stdout is a terminal."""
    cwd: str = field(default_factory=os.getcwd)
    """Working directory relative paths resolve against."""


class UsageError(Exception):
    """A bad command line: exit 64 with the usage text."""


class DataError(Exception):
    """A missing or malformed input file: exit 65."""


USAGE = f"""evalroom {CLI_VERSION}: human review statistics for LLM outputs

Usage:
  evalroom agreement <reviews.jsonl> --rubric <rubric.json> [--version <v>] [--min-items <n>]
  evalroom gate <run.jsonl> [--min-n 50] [--max-width 0.25] [--min-pass 0.9]
  evalroom compare <base.jsonl> <candidate.jsonl>
  evalroom golden promote <item-id> --items <items.jsonl> --reviews <reviews.jsonl>
                          --rubric <rubric.json> --out <golden.jsonl>
                          [--rules <rules.json>] [--by <name>] [--id <case-id>]
                          [--title <text>] [--allow-split] [--audit <audit.jsonl>]
  evalroom score <golden.jsonl> <outputs.jsonl> --out <run.jsonl> [--id <id>] [--label <label>]

Inspect logs (pip install "evalroom[inspect]"):
  evalroom gate <log.eval> [--scorer <name>] [--min-n 50] [--max-width 0.25] [--threshold 0.9]
                [--partial fail|pass|exclude]
  evalroom compare <a.eval> <b.eval> [--scorer <name>] [--partial fail|pass|exclude]
  evalroom judge-check <log.eval> [--scorer model_graded_qa] [--human <labels.jsonl|.csv>]
                       [--partial fail|pass|exclude] [--assume-unedited-agree]
                       [--min-agreement 0.8] [--min-n 50] [--max-width 0.25] [--top 10]

Every command accepts --json for machine-readable output and --help.

Exit codes:
  0  success / gate pass        1  gate fail, promotion refused, or broken cases in compare
  2  gate undecidable           64 usage error              65 bad or missing input file
"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _parser(*options: tuple[str, str]) -> _Parser:
    """``options`` are ``(flag, kind)`` with kind ``"string"`` or ``"boolean"``."""
    parser = _Parser(add_help=False, allow_abbrev=False, prog="evalroom")
    parser.add_argument("positionals", nargs="*")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    for flag, kind in options:
        if kind == "boolean":
            parser.add_argument(flag, action="store_true")
        else:
            parser.add_argument(flag, default=None)
    return parser


@dataclass(frozen=True, slots=True)
class Parsed:
    """Parsed command line: flag values by their ``--long-name`` and the positionals."""

    values: dict[str, Any]
    positionals: list[str]

    def get(self, flag: str) -> Any:
        """Value of ``--flag`` (``None`` when absent)."""
        return self.values.get(flag)


def parse(args: Sequence[str], *options: tuple[str, str]) -> Parsed:
    """Parses ``args`` strictly: unknown flags and missing flag values are usage errors."""
    parser = _parser(*options)
    namespace = parser.parse_intermixed_args(list(args))
    raw = vars(namespace)
    positionals = list(raw.pop("positionals"))
    values = {f"--{key.replace('_', '-')}": value for key, value in raw.items()}
    return Parsed(values=values, positionals=positionals)


def need(value: str | None, what: str) -> str:
    """``value``, or a usage error naming what is missing."""
    if value is None or value == "":
        raise UsageError(f"missing {what}")
    return value


_NUMBER = re.compile(r"^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$")


def num(value: str | None, flag: str, fallback: float) -> float:
    """Parses a numeric flag (``fallback`` when absent)."""
    if value is None:
        return fallback
    if not _NUMBER.match(value):
        raise UsageError(f'{flag} must be a number, got "{value}"')
    number = float(value)
    return int(number) if number.is_integer() else number


def resolve(io: CliIO, path: str) -> Path:
    """``path`` resolved against the CLI's working directory."""
    return Path(io.cwd, path)


def read_text(io: CliIO, path: str) -> str:
    """Reads a text file or raises :class:`DataError`."""
    try:
        return resolve(io, path).read_text(encoding="utf-8")
    except OSError as error:
        raise DataError(f"cannot read {path}: {error.strerror or error}") from error


def load_rubric(io: CliIO, path: str) -> Rubric:
    """Reads and validates a rubric file."""
    text = read_text(io, path)
    try:
        raw = json.loads(text)
    except ValueError as error:
        raise DataError(f"{path}: {error}") from error
    parsed = parse_rubric(raw)
    if isinstance(parsed, Err):
        raise DataError(f"{path}: invalid rubric: {'; '.join(parsed.error)}")
    return parsed.value


def lines(io: CliIO, path: str) -> list[JsonlLine]:
    """Reads a JSONL file or raises :class:`DataError` / :class:`JsonlParseError`."""
    try:
        return read_jsonl_lines(resolve(io, path))
    except JsonlParseError:
        raise
    except OSError as error:
        raise DataError(f"cannot read {path}: {error.strerror or error}") from error


def load_reviews(io: CliIO, path: str, rubric: Rubric) -> list[Review]:
    """Reads and validates a reviews file against a rubric."""
    reviews: list[Review] = []
    for entry in lines(io, path):
        parsed = validate_review(entry.value, rubric=rubric)
        if isinstance(parsed, Err):
            raise DataError(f"{path}:{entry.line}: {'; '.join(parsed.error)}")
        reviews.append(parsed.value)
    return reviews


def load_items(io: CliIO, path: str) -> list[Item]:
    """Reads and validates an items file."""
    items: list[Item] = []
    for entry in lines(io, path):
        parsed = parse_item(entry.value)
        if isinstance(parsed, Err):
            raise DataError(f"{path}:{entry.line}: {'; '.join(parsed.error)}")
        items.append(parsed.value)
    return items


def load_golden(io: CliIO, path: str, *, missing_ok: bool = False) -> list[GoldenCase]:
    """Reads a golden file; ``[]`` for a missing file when ``missing_ok``."""
    if missing_ok and not resolve(io, path).exists():
        return []
    cases: list[GoldenCase] = []
    for entry in lines(io, path):
        try:
            cases.append(golden_from_dict(entry.value))
        except ValueError as error:
            raise DataError(f"{path}:{entry.line}: {error}") from error
    return cases


def load_run(io: CliIO, path: str) -> EvalRun:
    """Reads a run file or raises :class:`DataError` / :class:`JsonlParseError`."""
    try:
        return read_run_file(resolve(io, path))
    except JsonlParseError:
        raise
    except OSError as error:
        raise DataError(f"cannot read {path}: {error.strerror or error}") from error


def emit_json(io: CliIO, value: Any) -> None:
    """Prints ``value`` as JSON with two-space indentation, as the TypeScript CLI does."""
    io.stdout(f"{stringify(value, indent=2)}\n")


Command = Callable[[Sequence[str], CliIO, Colors], int]


def main(argv: Sequence[str] | None = None, io: CliIO | None = None) -> int:
    """Runs the CLI with ``argv`` (without the program name) and returns the exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if io is None:
        io = CliIO(stdout=sys.stdout.write, stderr=sys.stderr.write, is_tty=sys.stdout.isatty())
    c = create_colors(color_enabled(io.is_tty, io.env))
    command, rest = (args[0], args[1:]) if args else (None, [])
    commands: dict[str, Command] = {
        "agreement": agreement_command,
        "gate": gate_command,
        "compare": compare_command,
        "golden": golden_command,
        "score": score_command,
        "judge-check": judge_check_command,
    }
    try:
        if command in commands:
            return commands[command](rest, io, c)
        if command in ("--version", "-v", "version"):
            io.stdout(f"{CLI_VERSION}\n")
            return EXIT.ok
        if command in (None, "help", "--help", "-h"):
            io.stdout(USAGE)
            return EXIT.usage if command is None else EXIT.ok
        raise UsageError(f'unknown command "{command}"')
    except UsageError as error:
        io.stderr(f"{c.red('error:')} {error}\n\n{USAGE}")
        return EXIT.usage
    except (DataError, JsonlParseError) as error:
        io.stderr(f"{c.red('error:')} {error}\n")
        return EXIT.data
    except OSError as error:
        io.stderr(f"{c.red('error:')} {error}\n")
        return EXIT.data


# ─── agreement ──────────────────────────────────────────────────────────────


def agreement_command(args: Sequence[str], io: CliIO, c: Colors) -> int:
    """``evalroom agreement``."""
    parsed = parse(args, ("--rubric", "string"), ("--version", "string"), ("--min-items", "string"))
    if parsed.get("--help"):
        io.stdout(USAGE)
        return EXIT.ok
    reviews_path = need(parsed.positionals[0] if parsed.positionals else None, "<reviews.jsonl>")
    rubric = load_rubric(io, need(parsed.get("--rubric"), "--rubric <rubric.json>"))
    reviews = load_reviews(io, reviews_path, rubric)
    report = agreement_report(
        reviews,
        rubric,
        rubric_version=parsed.get("--version"),
        min_items=int(num(parsed.get("--min-items"), "--min-items", 10)),
    )
    if parsed.get("--json"):
        emit_json(io, report.to_dict())
    else:
        io.stdout(render_agreement(report, c))
    return EXIT.ok


def _kappa_text(value: float | None, band: str | None, reason: str | None, c: Colors) -> str:
    if value is None:
        return c.dim(f"n/a ({reason or 'not computed'})")
    return f"{to_fixed(value, 2)}  {band or ''}"


def render_agreement(report: AgreementReport, c: Colors) -> str:
    """Renders an agreement report for the terminal."""
    out: list[str] = []
    out.append(
        f"{c.bold('evalroom agreement')} · rubric {c.cyan(report.rubric_version)} · "
        f"lenses {', '.join(report.lenses)}"
    )
    out.append(
        c.dim(
            f"{report.items} complete items · {report.incomplete_items} incomplete · "
            f"{report.excluded_reviews} reviews from other rubric versions excluded"
        )
    )
    out.append("")
    if report.median_seconds is None:
        median_text = c.dim("not recorded")
    else:
        per_lens = []
        for lens in report.lenses:
            seconds = report.median_seconds_by_lens.get(lens)
            per_lens.append(f"{lens} {'-' if seconds is None else f'{math_round(seconds)}s'}")
        median_text = f"{math_round(report.median_seconds)}s  ({' · '.join(per_lens)})"
    kappa = report.fleiss_kappa
    out.append(
        table(
            [
                ["Fleiss' kappa", _kappa_text(kappa.value, kappa.band, kappa.reason, c)],
                ["Raw agreement", format_interval(report.raw_agreement)],
                ["Median review", median_text],
            ]
        )
    )
    out.append("")
    if report.pairs:
        out.append(
            table(
                [
                    [
                        f"{pair.a} × {pair.b}",
                        format_interval(pair.agreement),
                        _kappa_text(pair.cohen_kappa.value, pair.cohen_kappa.band, None, c),
                    ]
                    for pair in report.pairs
                ],
                header=["Lens pair", "Agreement", "Cohen's kappa"],
            )
        )
        out.append("")
    out.append(
        table(
            [[verdict, format_interval(report.prevalence[verdict])] for verdict in VERDICTS],
            header=["Verdict", "Prevalence (of all lens verdicts)"],
        )
    )
    out.append("")
    if report.disagreements:
        out.append(c.bold(f"Disagreements ({len(report.disagreements)})"))
        rows = []
        for d in report.disagreements:
            cells = [d.item_id]
            for v in d.verdicts:
                label = f"{v.verdict} {v.error_class}" if v.error_class else v.verdict
                paint = (
                    c.green if v.verdict == "PASS" else c.red if v.verdict == "FAIL" else c.yellow
                )
                cells.append(paint(label))
            rows.append(cells)
        out.append(table(rows, header=["Item", *report.lenses]))
        out.append("")
    out.append(report.interpretation)
    return "\n".join(out) + "\n"


# ─── gate ───────────────────────────────────────────────────────────────────


def gate_command(args: Sequence[str], io: CliIO, c: Colors) -> int:
    """``evalroom gate`` on a run file, or on an Inspect log."""
    parsed = parse(
        args,
        ("--min-n", "string"),
        ("--max-width", "string"),
        ("--min-pass", "string"),
        ("--threshold", "string"),
        ("--scorer", "string"),
        ("--partial", "string"),
    )
    if parsed.get("--help"):
        io.stdout(USAGE)
        return EXIT.ok
    path = need(parsed.positionals[0] if parsed.positionals else None, "<run.jsonl>")
    bar_flag = "--threshold" if parsed.get("--threshold") is not None else "--min-pass"
    min_pass_rate = num(parsed.get(bar_flag), bar_flag, 0.9)
    if min_pass_rate < 0 or min_pass_rate > 1:
        raise UsageError(f"{bar_flag} must be between 0 and 1")
    min_n = num(parsed.get("--min-n"), "--min-n", 50)
    max_width = num(parsed.get("--max-width"), "--max-width", 0.25)
    if is_inspect_log(io, path):
        inspect_cli = _inspect_cli()
        return int(
            inspect_cli.gate_log_command(
                io, c, path, parsed, min_pass_rate=min_pass_rate, min_n=min_n, max_width=max_width
            )
        )
    _reject_inspect_flags(parsed)
    run = load_run(io, path)
    result = gate(run, min_pass_rate=min_pass_rate, min_n=min_n, max_width=max_width)
    if parsed.get("--json"):
        emit_json(
            io,
            {
                "run": {"id": run.id, "label": run.label, "rubricVersion": run.rubric_version},
                **result.to_dict(),
            },
        )
    else:
        io.stdout(render_gate(run.label, run.id, run.rubric_version, result, c))
    return gate_exit(result)


def gate_exit(result: GateResult) -> int:
    """0 / 1 / 2 for pass / fail / undecidable."""
    if result.decision == "pass":
        return EXIT.ok
    return EXIT.fail if result.decision == "fail" else EXIT.undecidable


def decision_label(decision: str, c: Colors) -> str:
    """``PASS`` / ``FAIL`` / ``UNDECIDABLE`` in colour."""
    if decision == "pass":
        return c.green("PASS")
    if decision == "fail":
        return c.red("FAIL")
    return c.yellow("UNDECIDABLE")


def render_gate(label: str, run_id: str, rubric_version: str, result: GateResult, c: Colors) -> str:
    """Renders a gate result for the terminal."""
    rows = [
        ["Decision", decision_label(result.decision, c)],
        ["Pass rate", format_interval(result.interval)],
        ["Bar", f"{percent(result.min_pass_rate)} on the lower bound"],
        ["Reason", result.reason],
    ]
    head = f"{c.bold('evalroom gate')} · run {c.cyan(label)} ({run_id}) · rubric {rubric_version}"
    return "\n".join([head, "", table(rows)]) + "\n"


# ─── compare ────────────────────────────────────────────────────────────────


def compare_command(args: Sequence[str], io: CliIO, c: Colors) -> int:
    """``evalroom compare`` on two run files, or on two Inspect logs."""
    parsed = parse(args, ("--scorer", "string"), ("--partial", "string"))
    if parsed.get("--help"):
        io.stdout(USAGE)
        return EXIT.ok
    pos = parsed.positionals
    base_path = need(pos[0] if pos else None, "<base.jsonl>")
    candidate_path = need(pos[1] if len(pos) > 1 else None, "<candidate.jsonl>")
    if is_inspect_log(io, base_path) or is_inspect_log(io, candidate_path):
        inspect_cli = _inspect_cli()
        return int(inspect_cli.compare_logs_command(io, c, base_path, candidate_path, parsed))
    _reject_inspect_flags(parsed)
    base = load_run(io, base_path)
    candidate = load_run(io, candidate_path)
    comparison = compare_runs(base, candidate)
    return emit_comparison(io, c, base, candidate, comparison, bool(parsed.get("--json")))


def emit_comparison(
    io: CliIO,
    c: Colors,
    base: EvalRun,
    candidate: EvalRun,
    comparison: RunComparison,
    as_json: bool,
) -> int:
    """Prints a comparison (text or JSON) and returns 1 when a case broke."""
    if as_json:
        emit_json(
            io,
            {
                "base": {
                    "id": base.id,
                    "label": base.label,
                    "summary": comparison.base_summary.to_dict(),
                },
                "candidate": {
                    "id": candidate.id,
                    "label": candidate.label,
                    "summary": comparison.candidate_summary.to_dict(),
                },
                "broken": [
                    {
                        "caseId": row.case_id,
                        "failures": [f.to_dict() for f in row.candidate.failures],
                    }
                    for row in comparison.broken
                ],
                "fixed": [
                    {"caseId": row.case_id, "failures": [f.to_dict() for f in row.base.failures]}
                    for row in comparison.fixed
                ],
                "same": [
                    {"caseId": row.case_id, "passed": row.base.passed} for row in comparison.same
                ],
                "onlyInBase": comparison.only_in_base,
                "onlyInCandidate": comparison.only_in_candidate,
                "rubricVersionMismatch": comparison.rubric_version_mismatch,
            },
        )
    else:
        io.stdout(render_comparison(base, candidate, comparison, c))
    return EXIT.fail if comparison.broken else EXIT.ok


def render_comparison(
    base: EvalRun, candidate: EvalRun, comparison: RunComparison, c: Colors
) -> str:
    """Renders a run comparison for the terminal."""
    still_failing = [row for row in comparison.same if not row.base.passed]
    out = [
        f"{c.bold('evalroom compare')} · {c.cyan(base.label)} → {c.cyan(candidate.label)}",
        "",
        table(
            [
                [base.label, format_interval(comparison.base_summary.pass_rate)],
                [candidate.label, format_interval(comparison.candidate_summary.pass_rate)],
            ]
        ),
        "",
        table(
            [
                [
                    c.red("broken"),
                    str(len(comparison.broken)),
                    ", ".join(r.case_id for r in comparison.broken),
                ],
                [
                    c.green("fixed"),
                    str(len(comparison.fixed)),
                    ", ".join(r.case_id for r in comparison.fixed),
                ],
                [
                    "still failing",
                    str(len(still_failing)),
                    ", ".join(r.case_id for r in still_failing),
                ],
                ["still passing", str(len(comparison.same) - len(still_failing)), ""],
            ],
            align=["left", "right", "left"],
        ),
    ]
    if comparison.broken:
        out.extend(["", c.bold("Broken cases")])
        out.append(
            table(
                [
                    [row.case_id, failure.code, failure.detail]
                    for row in comparison.broken
                    for failure in row.candidate.failures
                ]
            )
        )
    if comparison.only_in_base or comparison.only_in_candidate:
        out.extend(
            [
                "",
                c.yellow(
                    f"Not compared: {len(comparison.only_in_base)} only in {base.label}, "
                    f"{len(comparison.only_in_candidate)} only in {candidate.label}."
                ),
            ]
        )
    if comparison.rubric_version_mismatch:
        out.append(
            c.yellow(
                f"Warning: rubric versions differ ({base.rubric_version} vs "
                f"{candidate.rubric_version})."
            )
        )
    return "\n".join(out) + "\n"


# ─── golden promote ─────────────────────────────────────────────────────────


def golden_command(args: Sequence[str], io: CliIO, c: Colors) -> int:
    """``evalroom golden promote``."""
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub != "promote":
        raise UsageError(f'unknown golden subcommand "{sub}" (expected "promote")')
    parsed = parse(
        rest,
        ("--items", "string"),
        ("--reviews", "string"),
        ("--rubric", "string"),
        ("--out", "string"),
        ("--rules", "string"),
        ("--by", "string"),
        ("--id", "string"),
        ("--title", "string"),
        ("--allow-split", "boolean"),
        ("--audit", "string"),
    )
    if parsed.get("--help"):
        io.stdout(USAGE)
        return EXIT.ok
    item_id = need(parsed.positionals[0] if parsed.positionals else None, "<item-id>")
    out = need(parsed.get("--out"), "--out <golden.jsonl>")
    rubric = load_rubric(io, need(parsed.get("--rubric"), "--rubric <rubric.json>"))
    items_path = need(parsed.get("--items"), "--items <items.jsonl>")
    items = load_items(io, items_path)
    reviews = load_reviews(io, need(parsed.get("--reviews"), "--reviews <reviews.jsonl>"), rubric)
    item = next((candidate for candidate in items if candidate.id == item_id), None)
    if item is None:
        raise DataError(f'item "{item_id}" is not in {items_path}')

    rules: list[PathRule] = []
    rules_path = parsed.get("--rules")
    if rules_path is not None:
        text = read_text(io, rules_path)
        try:
            raw = json.loads(text)
        except ValueError as error:
            raise DataError(f"{rules_path}: {error}") from error
        parsed_rules = parse_rules(raw)
        if isinstance(parsed_rules, Err):
            raise DataError(f"{rules_path}: {'; '.join(parsed_rules.error)}")
        rules = parsed_rules.value
    elif item.meta is not None and isinstance(item.meta.get("expectedRules"), list):
        parsed_rules = parse_rules(item.meta["expectedRules"])
        if isinstance(parsed_rules, Err):
            raise DataError(f"item {item_id} meta.expectedRules: {'; '.join(parsed_rules.error)}")
        rules = parsed_rules.value

    promoted_by = parsed.get("--by")
    if promoted_by is None:
        promoted_by = io.env.get("USER") or io.env.get("USERNAME") or "cli"
    options: dict[str, Any] = {
        "rubric": rubric,
        "expected_rules": rules,
        "promoted_by": promoted_by,
        "require_unanimous_pass": not parsed.get("--allow-split"),
        "id": parsed.get("--id"),
        "title": parsed.get("--title"),
    }
    preview = promote_to_golden(item, reviews, **options)
    if isinstance(preview, Ok):
        existing = load_golden(io, out, missing_ok=True)
        if any(golden.id == preview.value.id for golden in existing):
            raise DataError(f'golden case "{preview.value.id}" already exists in {out}')
    audit_path = parsed.get("--audit")
    if audit_path is not None:
        result = promote_and_record(
            item, reviews, JsonlAuditSink(resolve(io, audit_path)), **options
        )
    else:
        result = preview
    if isinstance(result, Ok):
        append_jsonl(resolve(io, out), [result.value])

    if parsed.get("--json"):
        if isinstance(result, Ok):
            emit_json(io, {"ok": True, "golden": result.value.to_dict()})
        else:
            emit_json(io, {"ok": False, "refusals": [r.to_dict() for r in result.error]})
    elif isinstance(result, Ok):
        golden = result.value
        io.stdout(
            f"{c.green('promoted')} {golden.id} (item {item_id}, {len(golden.expected_rules)} "
            f"rules, reviews {', '.join(golden.provenance.review_ids)}) → {out}\n"
        )
    else:
        io.stdout(f"{c.red('refused')} item {item_id}:\n")
        for refusal in result.error:
            io.stdout(f"  {c.bold(refusal.code)}  {refusal.message}\n")
    return EXIT.ok if isinstance(result, Ok) else EXIT.fail


# ─── score ──────────────────────────────────────────────────────────────────


def score_command(args: Sequence[str], io: CliIO, c: Colors) -> int:
    """``evalroom score``."""
    parsed = parse(args, ("--out", "string"), ("--id", "string"), ("--label", "string"))
    if parsed.get("--help"):
        io.stdout(USAGE)
        return EXIT.ok
    pos = parsed.positionals
    golden_path = need(pos[0] if pos else None, "<golden.jsonl>")
    outputs_path = need(pos[1] if len(pos) > 1 else None, "<outputs.jsonl>")
    out = need(parsed.get("--out"), "--out <run.jsonl>")
    cases = load_golden(io, golden_path)
    outputs: dict[str, Any] = {}
    for entry in lines(io, outputs_path):
        value = entry.value
        if (
            not is_record(value)
            or not isinstance(value.get("caseId"), str)
            or "output" not in value
        ):
            raise DataError(
                f"{outputs_path}:{entry.line}: each line needs a string caseId and an output"
            )
        if value["caseId"] in outputs:
            raise DataError(f'{outputs_path}:{entry.line}: duplicate caseId "{value["caseId"]}"')
        outputs[value["caseId"]] = value["output"]
    run_id = parsed.get("--id")
    if run_id is None:
        run_id = f"run-{re.sub(r'[:.]', '-', now_iso())}"
    label = parsed.get("--label")
    run = score_run(cases, outputs, id=run_id, label=run_id if label is None else label)
    write_run_file(resolve(io, out), run)
    summary = summarize(run)
    known = {golden.id for golden in cases}
    unknown = [case_id for case_id in outputs if case_id not in known]
    if parsed.get("--json"):
        emit_json(
            io,
            {
                "id": run.id,
                "label": run.label,
                "out": out,
                "summary": summary.to_dict(),
                "ignoredOutputs": unknown,
            },
        )
    else:
        io.stdout(
            f"{c.bold('evalroom score')} · {run.label} · "
            f"{format_interval(summary.pass_rate)} → {out}\n"
        )
        codes = sorted(summary.failures_by_code.items(), key=lambda kv: -kv[1])
        if codes:
            io.stdout(f"{table([[code, str(count)] for code, count in codes])}\n")
        if unknown:
            io.stdout(
                c.yellow(
                    f"Ignored {len(unknown)} outputs with no golden case: {', '.join(unknown)}\n"
                )
            )
    return EXIT.ok


# ─── Inspect logs ───────────────────────────────────────────────────────────

_INSPECT_FLAGS = ("--scorer", "--partial")


def _reject_inspect_flags(parsed: Parsed) -> None:
    for flag in _INSPECT_FLAGS:
        if parsed.get(flag) is not None:
            raise UsageError(f"{flag} only applies to Inspect logs (.eval or Inspect .json)")
    if parsed.get("--threshold") is not None:
        raise UsageError("--threshold only applies to Inspect logs; use --min-pass for run files")


def is_inspect_log(io: CliIO, path: str) -> bool:
    """``True`` for an Inspect log: a ``.eval`` file, or a ``.json`` file shaped like one."""
    lower = path.lower()
    if lower.endswith(".eval"):
        return True
    if not lower.endswith(".json"):
        return False
    try:
        with resolve(io, path).open(encoding="utf-8") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    stripped = head.lstrip()
    return stripped.startswith("{") and '"eval"' in head and '"version"' in head


def _inspect_cli() -> Any:
    try:
        from ..inspect import _cli as inspect_cli  # noqa: PLC0415 - optional extra
    except ImportError as error:
        raise UsageError(
            "reading Inspect logs needs the Inspect extra: install evalroom[inspect] "
            '(pip install "evalroom[inspect]"; Python 3.11+)'
        ) from error
    return inspect_cli


def judge_check_command(args: Sequence[str], io: CliIO, c: Colors) -> int:
    """``evalroom judge-check`` (Inspect extra)."""
    return int(_inspect_cli().judge_check_command(args, io, c))
