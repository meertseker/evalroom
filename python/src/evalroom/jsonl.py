"""Newline-delimited JSON helpers. Files are append-only; nothing is rewritten in place."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._js import stringify
from ._serde import to_json

__all__ = [
    "JsonlLine",
    "JsonlParseError",
    "append_jsonl",
    "parse_jsonl",
    "parse_jsonl_lines",
    "read_jsonl",
    "read_jsonl_lines",
    "to_jsonl",
]


class JsonlParseError(ValueError):
    """Raised when a JSONL line is not valid JSON or not the expected shape."""

    source: str
    """File (or label) the line came from."""
    line: int
    """1-based line number."""

    def __init__(self, source: str, line: int, message: str) -> None:
        super().__init__(f"{source}:{line}: {message}")
        self.source = source
        self.line = line


@dataclass(frozen=True, slots=True)
class JsonlLine:
    """One parsed line with its 1-based line number."""

    line: int
    value: Any


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def parse_jsonl_lines(text: str, source: str = "<input>") -> list[JsonlLine]:
    """Parses JSONL text. Blank lines are skipped; a UTF-8 BOM and CRLF endings are accepted.

    :raises JsonlParseError: with the source and line number on invalid JSON.
    """
    if text.startswith("﻿"):
        text = text[1:]
    out: list[JsonlLine] = []
    for index, raw in enumerate(text.split("\n")):
        line = raw[:-1] if raw.endswith("\r") else raw
        if line.strip() == "":
            continue
        try:
            value = json.loads(line, parse_constant=_reject_constant)
        except ValueError as error:
            raise JsonlParseError(source, index + 1, str(error)) from error
        out.append(JsonlLine(index + 1, value))
    return out


def parse_jsonl(text: str, source: str = "<input>") -> list[Any]:
    """:func:`parse_jsonl_lines`, values only."""
    return [entry.value for entry in parse_jsonl_lines(text, source)]


def to_jsonl(records: Iterable[Any]) -> str:
    """Serialises records as JSONL: one compact JSON value per line, trailing newline.

    Dataclasses are converted to their camelCase JSON shape first.
    """
    return "".join(f"{stringify(to_json(record))}\n" for record in records)


def read_jsonl_lines(path: str | Path, *, missing_ok: bool = False) -> list[JsonlLine]:
    """Reads a JSONL file. A missing file is an error unless ``missing_ok``, then ``[]``."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        if missing_ok:
            return []
        raise
    return parse_jsonl_lines(text, str(path))


def read_jsonl(path: str | Path, *, missing_ok: bool = False) -> list[Any]:
    """:func:`read_jsonl_lines`, values only."""
    return [entry.value for entry in read_jsonl_lines(path, missing_ok=missing_ok)]


def append_jsonl(path: str | Path, records: Iterable[Any]) -> None:
    """Appends records to a JSONL file, creating it and its directory if needed."""
    text = to_jsonl(records)
    if not text:
        return
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
