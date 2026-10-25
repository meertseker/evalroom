"""Newline-delimited JSON persistence and the run-file format the CLI uses.

Layout of a :class:`JsonlStore` directory (identical to the TypeScript implementation)::

    items.jsonl        one Item per line
    reviews.jsonl      one Review per line
    golden.jsonl       one GoldenCase per line
    calibration.jsonl  one CalibrationVersion per line; the last line per id is current
    runs/<id>.jsonl    one run file per EvalRun (see format_run_jsonl)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Generic, TypeVar

from .._serde import MISSING
from ..calibration import CalibrationVersion, calibration_version_from_dict
from ..evalrun import CaseResult, EvalRun
from ..golden import GoldenCase, golden_from_dict
from ..jsonl import (
    JsonlLine,
    JsonlParseError,
    append_jsonl,
    parse_jsonl,
    parse_jsonl_lines,
    read_jsonl,
    read_jsonl_lines,
    to_jsonl,
)
from ..result import Err, is_record
from ..review import Item, Review, parse_item, validate_review
from ..rules import RuleFailure
from .types import DuplicateIdError, matches_review, matches_tenant

__all__ = [
    "JsonlLine",
    "JsonlParseError",
    "JsonlStore",
    "append_jsonl",
    "format_run_jsonl",
    "parse_jsonl",
    "parse_jsonl_lines",
    "parse_run_jsonl",
    "read_jsonl",
    "read_jsonl_lines",
    "read_run_file",
    "to_jsonl",
    "write_run_file",
]

T = TypeVar("T")


def format_run_jsonl(run: EvalRun) -> str:
    """Serialises a run as a run file.

    A ``{"kind":"run",...}`` header line followed by one :class:`CaseResult` per line.
    """
    header: dict[str, Any] = {
        "kind": "run",
        "id": run.id,
        "label": run.label,
        "rubricVersion": run.rubric_version,
    }
    if run.created_at is not None:
        header["createdAt"] = run.created_at
    if run.tenant is not None:
        header["tenant"] = run.tenant
    return to_jsonl([header, *run.results])


def _parse_failures(value: Any, source: str, line: int) -> list[RuleFailure]:
    if value is MISSING:
        return []
    if not isinstance(value, list):
        raise JsonlParseError(source, line, "failures must be an array")
    failures: list[RuleFailure] = []
    for failure in value:
        if not is_record(failure) or not isinstance(failure.get("code"), str):
            raise JsonlParseError(source, line, "each failure needs a string code")
        path = failure.get("path")
        detail = failure.get("detail")
        failures.append(
            RuleFailure(
                code=failure["code"],
                path=path if isinstance(path, str) else "",
                detail=detail if isinstance(detail, str) else "",
            )
        )
    return failures


def parse_run_jsonl(
    text: str,
    source: str = "<input>",
    *,
    fallback_id: str | None = None,
    fallback_label: str | None = None,
) -> EvalRun:
    """Parses a run file.

    The header line is optional: without it, the fallbacks (or the source file name) supply id
    and label and the rubric version is ``"unknown"``.

    :raises JsonlParseError: on invalid JSON or a malformed result line.
    """
    lines = parse_jsonl_lines(text, source)
    header: dict[str, Any] = {}
    results: list[CaseResult] = []
    seen: set[str] = set()
    for index, entry in enumerate(lines):
        value = entry.value
        if not is_record(value):
            raise JsonlParseError(source, entry.line, "each line must be a JSON object")
        if value.get("kind") == "run":
            if index != 0:
                raise JsonlParseError(source, entry.line, "the run header must be the first line")
            header = value
            continue
        case_id = value.get("caseId")
        if not isinstance(case_id, str) or case_id == "":
            raise JsonlParseError(source, entry.line, "caseId must be a non-empty string")
        if not isinstance(value.get("passed"), bool):
            raise JsonlParseError(source, entry.line, "passed must be a boolean")
        if case_id in seen:
            raise JsonlParseError(source, entry.line, f'duplicate caseId "{case_id}"')
        seen.add(case_id)
        results.append(
            CaseResult(
                case_id=case_id,
                passed=value["passed"],
                failures=_parse_failures(value.get("failures", MISSING), source, entry.line),
                output=value.get("output"),
            )
        )
    name = Path(source).stem
    return EvalRun(
        id=header["id"] if isinstance(header.get("id"), str) else (fallback_id or name),
        label=header["label"] if isinstance(header.get("label"), str) else (fallback_label or name),
        rubric_version=(
            header["rubricVersion"] if isinstance(header.get("rubricVersion"), str) else "unknown"
        ),
        results=results,
        created_at=header["createdAt"] if isinstance(header.get("createdAt"), str) else None,
        tenant=header["tenant"] if isinstance(header.get("tenant"), str) else None,
    )


def read_run_file(path: str | Path) -> EvalRun:
    """Reads a run file from disk. See :func:`parse_run_jsonl`."""
    return parse_run_jsonl(Path(path).read_text(encoding="utf-8"), str(path))


def write_run_file(path: str | Path, run: EvalRun) -> None:
    """Writes a run file, replacing any file at ``path``. See :func:`format_run_jsonl`."""
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(format_run_jsonl(run), encoding="utf-8", newline="\n")


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _review_from(raw: Any) -> Review:
    parsed = validate_review(raw, require_fail_details=False)
    if isinstance(parsed, Err):
        raise ValueError("; ".join(parsed.error))
    return parsed.value


def _item_from(raw: Any) -> Item:
    parsed = parse_item(raw)
    if isinstance(parsed, Err):
        raise ValueError("; ".join(parsed.error))
    return parsed.value


class JsonlStore(Generic[T]):
    """A file-backed store over JSONL files in one directory.

    Implements :class:`EvalStore` and :class:`CalibrationStore`. Every write appends;
    duplicates are detected by reading the file first, so run a single writer per directory.
    """

    def __init__(self, dir: str | Path) -> None:
        self.dir = Path(dir)
        """Directory holding the files. Created on first write."""

    def _file(self, name: str) -> Path:
        return self.dir / name

    def _all(self, name: str) -> list[Any]:
        return read_jsonl(self._file(name), missing_ok=True)

    def _insert(self, name: str, kind: str, id: str, record: Any) -> None:
        if any(is_record(row) and row.get("id") == id for row in self._all(name)):
            raise DuplicateIdError(kind, id)
        append_jsonl(self._file(name), [record])

    def add_item(self, item: Item) -> None:
        """Adds an item."""
        self._insert("items.jsonl", "item", item.id, item)

    def get_item(self, id: str) -> Item | None:
        """The item with this id, if any."""
        return next((i for i in self.list_items() if i.id == id), None)

    def list_items(self, *, tenant: str | None = None) -> list[Item]:
        """All items, optionally for one tenant."""
        items = [_item_from(raw) for raw in self._all("items.jsonl")]
        return [item for item in items if matches_tenant(item.tenant, tenant)]

    def add_review(self, review: Review) -> None:
        """Adds a review."""
        self._insert("reviews.jsonl", "review", review.id, review)

    def list_reviews(
        self,
        *,
        item_id: str | None = None,
        lens: str | None = None,
        rubric_version: str | None = None,
        tenant: str | None = None,
    ) -> list[Review]:
        """Reviews matching the filter."""
        reviews = [_review_from(raw) for raw in self._all("reviews.jsonl")]
        return [
            review
            for review in reviews
            if matches_review(
                review, item_id=item_id, lens=lens, rubric_version=rubric_version, tenant=tenant
            )
        ]

    def add_golden(self, golden: GoldenCase) -> None:
        """Adds a golden case."""
        self._insert("golden.jsonl", "golden case", golden.id, golden)

    def get_golden(self, id: str) -> GoldenCase | None:
        """The golden case with this id, if any."""
        return next((g for g in self.list_golden() if g.id == id), None)

    def list_golden(self, *, tenant: str | None = None) -> list[GoldenCase]:
        """All golden cases, optionally for one tenant."""
        cases = [golden_from_dict(raw) for raw in self._all("golden.jsonl")]
        return [case for case in cases if matches_tenant(case.tenant, tenant)]

    def _run_path(self, id: str) -> Path:
        if not _SAFE_ID.match(id):
            raise ValueError(
                f'run id "{id}" must match {_SAFE_ID.pattern} to be used as a file name'
            )
        return self.dir / "runs" / f"{id}.jsonl"

    def add_run(self, run: EvalRun) -> None:
        """Adds an eval run as ``runs/<id>.jsonl``; refuses an existing id."""
        path = self._run_path(run.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(format_run_jsonl(run))
        except FileExistsError as error:
            raise DuplicateIdError("run", run.id) from error

    def get_run(self, id: str) -> EvalRun | None:
        """The run with this id, if any."""
        path = self._run_path(id)
        if not path.exists():
            return None
        return read_run_file(path)

    def list_runs(self, *, tenant: str | None = None) -> list[EvalRun]:
        """All runs, sorted by file name, optionally for one tenant."""
        folder = self.dir / "runs"
        if not folder.is_dir():
            return []
        runs = [read_run_file(path) for path in sorted(folder.glob("*.jsonl"))]
        return [run for run in runs if matches_tenant(run.tenant, tenant)]

    def list_versions(self) -> list[CalibrationVersion[T]]:
        """Every calibration version, current state (last line per id), by ``version``."""
        latest: dict[str, CalibrationVersion[T]] = {}
        for raw in self._all("calibration.jsonl"):
            version: CalibrationVersion[T] = calibration_version_from_dict(raw)
            latest[version.id] = version
        return sorted(latest.values(), key=lambda v: v.version)

    def get_version(self, id: str) -> CalibrationVersion[T] | None:
        """The calibration version with this id, if any."""
        return next((v for v in self.list_versions() if v.id == id), None)

    def save_version(self, version: CalibrationVersion[T]) -> None:
        """Appends the version; the last line per id is its current state."""
        append_jsonl(self._file("calibration.jsonl"), [version])
