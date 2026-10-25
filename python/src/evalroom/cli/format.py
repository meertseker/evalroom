"""Terminal formatting for the CLI: ANSI colour (NO_COLOR / FORCE_COLOR) and text tables."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

__all__ = ["Colors", "color_enabled", "create_colors", "strip_ansi", "table", "visible_length"]


def _wrap(open_code: int, close_code: int, enabled: bool) -> Callable[[str], str]:
    if not enabled:
        return lambda text: text
    return lambda text: f"\x1b[{open_code}m{text}\x1b[{close_code}m"


@dataclass(frozen=True, slots=True)
class Colors:
    """Colour helpers. Every function is the identity when colour is disabled."""

    bold: Callable[[str], str]
    dim: Callable[[str], str]
    red: Callable[[str], str]
    green: Callable[[str], str]
    yellow: Callable[[str], str]
    cyan: Callable[[str], str]


def create_colors(enabled: bool) -> Colors:
    """Builds :class:`Colors`; all identity functions when ``enabled`` is false."""
    return Colors(
        bold=_wrap(1, 22, enabled),
        dim=_wrap(2, 22, enabled),
        red=_wrap(31, 39, enabled),
        green=_wrap(32, 39, enabled),
        yellow=_wrap(33, 39, enabled),
        cyan=_wrap(36, 39, enabled),
    )


def color_enabled(is_tty: bool, env: Mapping[str, str]) -> bool:
    """``NO_COLOR`` (any non-empty value) disables, ``FORCE_COLOR`` (not ``"0"``) enables,
    otherwise colour follows whether the stream is a TTY."""
    if env.get("NO_COLOR", "") != "":
        return False
    if "FORCE_COLOR" in env and env["FORCE_COLOR"] != "0":
        return True
    return is_tty


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Removes ANSI escape codes."""
    return _ANSI.sub("", text)


def visible_length(text: str) -> int:
    """Length of ``text`` as displayed, ignoring ANSI escape codes."""
    return len(strip_ansi(text))


def table(
    rows: Sequence[Sequence[str]],
    *,
    header: Sequence[str] | None = None,
    align: Sequence[Literal["left", "right"]] | None = None,
    indent: str = "  ",
    gap: str = "  ",
) -> str:
    """Renders rows as a fixed-width text table. ANSI codes do not disturb alignment."""
    all_rows = [list(header), *rows] if header else list(rows)
    widths: list[int] = []
    for row in all_rows:
        for i, cell in enumerate(row):
            if i >= len(widths):
                widths.append(0)
            widths[i] = max(widths[i], visible_length(cell))

    def side(i: int) -> str:
        return align[i] if align is not None and i < len(align) else "left"

    def render(row: Sequence[str]) -> str:
        cells: list[str] = []
        for i, cell in enumerate(row):
            pad = " " * (widths[i] - visible_length(cell))
            if i == len(row) - 1 and side(i) != "right":
                cells.append(cell)
            elif side(i) == "right":
                cells.append(pad + cell)
            else:
                cells.append(cell + pad)
        return indent + gap.join(cells)

    lines: list[str] = []
    if header:
        lines.append(render(header))
        lines.append(indent + gap.join("─" * width for width in widths))
    lines.extend(render(row) for row in rows)
    return "\n".join(line.rstrip() for line in lines)
