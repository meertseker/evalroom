from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from evalroom.cli import EXIT, CliIO, main
from evalroom.cli.format import strip_ansi, table, visible_length
from helpers import DATA


@dataclass
class Ran:
    code: int
    stdout: str
    stderr: str


def cli(args: list[str], cwd: Path = DATA, **env: str) -> Ran:
    out: list[str] = []
    err: list[str] = []
    io = CliIO(
        stdout=out.append,
        stderr=err.append,
        env={"NO_COLOR": "1", **env},
        is_tty=False,
        cwd=str(cwd),
    )
    code = main(args, io)
    return Ran(code, "".join(out), "".join(err))


def test_agreement_prints_kappa_interval_pairs_and_disagreements() -> None:
    ran = cli(["agreement", "reviews.jsonl", "--rubric", "rubric.json"])
    assert ran.code == EXIT.ok
    assert "rubric 2026-10.1" in ran.stdout
    assert (
        "19 complete items · 1 incomplete · 2 reviews from other rubric versions excluded"
        in ran.stdout
    )
    assert re.search(r"Raw agreement\s+58% \(11/19\) · 36%–77% · not decidable", ran.stdout)
    assert "Disagreements (8)" in ran.stdout
    assert "\x1b[" not in ran.stdout


def test_agreement_json_and_version_flag() -> None:
    report = json.loads(
        cli(["agreement", "reviews.jsonl", "--rubric", "rubric.json", "--json"]).stdout
    )
    assert report["items"] == 19
    assert (report["rawAgreement"]["k"], report["rawAgreement"]["n"]) == (11, 19)
    old = json.loads(
        cli(
            [
                "agreement",
                "reviews.jsonl",
                "--rubric",
                "rubric.json",
                "--version",
                "2026-09.3",
                "--json",
            ]
        ).stdout
    )
    assert old["items"] == 0
    assert old["incompleteItems"] == 1


def test_usage_and_data_errors(tmp_path: Path) -> None:
    assert cli(["agreement", "reviews.jsonl"]).code == EXIT.usage
    assert cli(["frobnicate"]).code == EXIT.usage
    assert (
        cli(["agreement", "reviews.jsonl", "--rubric", "rubric.json", "--nope"]).code == EXIT.usage
    )
    assert cli(["gate", "run-base.jsonl", "--min-pass", "lots"]).code == EXIT.usage
    assert cli(["gate", "run-base.jsonl", "--scorer", "x"]).code == EXIT.usage
    assert cli(["agreement", "nope.jsonl", "--rubric", "rubric.json"]).code == EXIT.data
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"id":"x","itemId":"t-01","lens":"tone","verdict":"FAIL","reviewer":"b",'
        '"rubricVersion":"2026-10.1","createdAt":"2026-10-01T00:00:00Z"}\n',
        encoding="utf-8",
    )
    ran = cli(["agreement", str(bad), "--rubric", "rubric.json"])
    assert ran.code == EXIT.data
    assert "bad.jsonl:1: a FAIL requires errorClass" in ran.stderr


def test_help_version_and_no_command() -> None:
    assert cli([]).code == EXIT.usage
    helped = cli(["--help"])
    assert helped.code == EXIT.ok
    assert "evalroom judge-check" in helped.stdout
    assert cli(["gate", "--help"]).code == EXIT.ok
    assert cli(["--version"]).stdout == "0.1.0\n"


def test_gate_exit_codes() -> None:
    undecidable = cli(["gate", "run-candidate.jsonl", "--min-pass", "0.8"])
    assert undecidable.code == EXIT.undecidable
    assert "UNDECIDABLE" in undecidable.stdout
    loose = ["--min-n", "5", "--max-width", "1"]
    assert cli(["gate", "run-candidate.jsonl", "--min-pass", "0.5", *loose]).code == EXIT.ok
    failed = cli(["gate", "run-base.jsonl", "--min-pass", "0.9", *loose, "--json"])
    assert failed.code == EXIT.fail
    data = json.loads(failed.stdout)
    assert data["decision"] == "fail"
    assert (data["interval"]["k"], data["interval"]["n"]) == (7, 11)
    assert data["run"] == {"id": "base", "label": "reply-v3", "rubricVersion": "2026-10.1"}
    assert cli(["gate", "run-base.jsonl", "--min-pass", "2"]).code == EXIT.usage


def test_compare_lists_broken_and_fixed() -> None:
    ran = cli(["compare", "run-base.jsonl", "run-candidate.jsonl"])
    assert ran.code == EXIT.fail
    assert re.search(r"broken\s+1\s+golden-t-08", ran.stdout)
    assert re.search(r"fixed\s+3\s+golden-t-02, golden-t-06, golden-t-17", ran.stdout)
    same = json.loads(cli(["compare", "run-base.jsonl", "run-base.jsonl", "--json"]).stdout)
    assert same["broken"] == []


def test_score_writes_a_run_file_gate_and_compare_can_read(tmp_path: Path) -> None:
    out = tmp_path / "run.jsonl"
    ran = cli(
        [
            "score",
            "golden.jsonl",
            "outputs-candidate.jsonl",
            "--out",
            str(out),
            "--id",
            "c2",
            "--label",
            "again",
        ]
    )
    assert ran.code == EXIT.ok
    assert "82% (9/11)" in ran.stdout
    header = json.loads(out.read_text(encoding="utf-8").split("\n")[0])
    assert {k: header[k] for k in ("kind", "id", "label", "rubricVersion")} == {
        "kind": "run",
        "id": "c2",
        "label": "again",
        "rubricVersion": "2026-10.1",
    }
    assert cli(["compare", "run-candidate.jsonl", str(out)]).code == EXIT.ok
    expected = (DATA / "run-candidate.jsonl").read_text(encoding="utf-8").split("\n")[1:]
    assert out.read_text(encoding="utf-8").split("\n")[1:] == expected


def test_golden_promote_appends_audits_and_refuses(tmp_path: Path) -> None:
    out = tmp_path / "golden.jsonl"
    audit = tmp_path / "audit.jsonl"
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps([{"path": "reply", "op": "notContains", "value": "guarantee"}]))
    base = [
        "golden", "promote", "t-01",
        "--items", "items.jsonl", "--reviews", "reviews.jsonl", "--rubric", "rubric.json",
        "--out", str(out), "--rules", str(rules), "--by", "qa",
    ]  # fmt: skip
    promoted = cli([*base, "--audit", str(audit)])
    assert promoted.code == EXIT.ok
    assert "promoted golden-t-01" in promoted.stdout
    case = json.loads(out.read_text(encoding="utf-8"))
    assert case["id"] == "golden-t-01"
    assert case["provenance"]["promotedBy"] == "qa"
    event = json.loads(audit.read_text(encoding="utf-8"))
    assert (event["action"], event["actor"]) == ("golden.promote", "qa")
    assert cli(base).code == EXIT.data

    split = cli([*base[:2], "t-03", *base[3:], "--json"])
    assert split.code == EXIT.fail
    assert json.loads(split.stdout)["refusals"][0]["code"] == "NOT_UNANIMOUS_PASS"
    no_rules = cli(
        ["golden", "promote", "t-02", "--items", "items.jsonl", "--reviews", "reviews.jsonl",
         "--rubric", "rubric.json", "--out", str(out)]
    )  # fmt: skip
    assert "NO_RULES" in no_rules.stdout
    assert cli(["golden", "demote"]).code == EXIT.usage


def test_table_aligns_by_visible_width() -> None:
    red = "\x1b[31mFAIL\x1b[39m"
    assert visible_length(red) == 4
    assert strip_ansi(table([["a", red, "x"], ["longer", "PASS", "y"]])) == (
        "  a       FAIL  x\n  longer  PASS  y"
    )


def test_colour_follows_force_color() -> None:
    io_out: list[str] = []
    io = CliIO(stdout=io_out.append, stderr=io_out.append, env={"FORCE_COLOR": "1"}, cwd=str(DATA))
    main(["gate", "run-candidate.jsonl"], io)
    assert "\x1b[" in "".join(io_out)


def test_console_script_end_to_end(tmp_path: Path) -> None:
    exe = shutil.which("evalroom", path=str(Path(sys.executable).parent))
    command = [exe] if exe else [sys.executable, "-m", "evalroom"]
    env = {**os.environ, "NO_COLOR": "1"}
    agreement = subprocess.run(
        [*command, "agreement", "reviews.jsonl", "--rubric", "rubric.json"],
        cwd=DATA, env=env, capture_output=True, text=True, encoding="utf-8", check=False,
    )  # fmt: skip
    assert agreement.returncode == 0
    assert "Fleiss' kappa" in agreement.stdout
    version = subprocess.run(
        [*command, "--version"], capture_output=True, text=True, check=False, env=env
    )
    assert version.stdout.strip() == "0.1.0"
    shutil.copy(DATA / "run-candidate.jsonl", tmp_path / "run.jsonl")
    gated = subprocess.run(
        [*command, "gate", str(tmp_path / "run.jsonl")], capture_output=True, check=False, env=env
    )
    assert gated.returncode == EXIT.undecidable


@pytest.mark.parametrize("flag", ["--min-n=5", "--min-n", "--max-width=0.5"])
def test_flag_forms(flag: str) -> None:
    args = ["gate", "run-candidate.jsonl", flag]
    if flag == "--min-n":
        args.append("5")
    assert cli(args).code in (EXIT.ok, EXIT.fail, EXIT.undecidable)
