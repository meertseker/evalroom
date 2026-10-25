"""DataFrame helpers (``pip install "evalroom[pandas]"``).

:func:`to_dataframe` turns evalroom records into a :class:`pandas.DataFrame` with snake_case
columns, one row per record: reviews, items, case results, golden cases, an
:class:`~evalroom.evalrun.EvalRun` (its results), an agreement report (its disagreements, one
row per lens verdict) or a judge-check report (its disagreements).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ._serde import MISSING
from .agreement import AgreementReport
from .evalrun import CaseResult, EvalRun

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["to_dataframe"]


def _row(record: Any) -> dict[str, Any]:
    if isinstance(record, CaseResult):
        return {
            "case_id": record.case_id,
            "passed": record.passed,
            "failure_codes": [f.code for f in record.failures],
            "failures": [f.detail for f in record.failures],
            "output": record.output,
        }
    if dataclasses.is_dataclass(record) and not isinstance(record, type):
        row: dict[str, Any] = {}
        for f in dataclasses.fields(record):
            value = getattr(record, f.name)
            if value is MISSING:
                value = None
            elif dataclasses.is_dataclass(value) and not isinstance(value, type):
                value = dataclasses.asdict(value)
            elif isinstance(value, (list, tuple)):
                value = [
                    dataclasses.asdict(v)
                    if dataclasses.is_dataclass(v) and not isinstance(v, type)
                    else v
                    for v in value
                ]
            row[f.name] = value
        return row
    if isinstance(record, dict):
        return dict(record)
    raise TypeError(f"cannot turn {type(record).__name__} into a DataFrame row")


def to_dataframe(records: Iterable[Any] | EvalRun | AgreementReport | Any) -> pd.DataFrame:
    """A DataFrame with one row per record. See the module docstring for what is accepted."""
    try:
        import pandas as pd  # noqa: PLC0415 - optional extra
    except ImportError as error:  # pragma: no cover - the extra is installed in development
        raise ImportError('to_dataframe needs pandas: pip install "evalroom[pandas]"') from error
    rows: list[dict[str, Any]]
    if isinstance(records, EvalRun):
        rows = [_row(result) for result in records.results]
    elif isinstance(records, AgreementReport):
        rows = [
            {"item_id": d.item_id, **_row(verdict)}
            for d in records.disagreements
            for verdict in d.verdicts
        ]
    elif hasattr(records, "disagreements") and hasattr(records, "confusion"):
        rows = [_row(d) for d in records.disagreements]
    else:
        rows = [_row(record) for record in records]
    frame: pd.DataFrame = pd.DataFrame(rows)
    return frame
