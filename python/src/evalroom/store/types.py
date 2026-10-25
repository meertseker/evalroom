"""Storage interfaces.

evalroom has no database: these protocols are the whole persistence contract, and
:class:`~evalroom.store.MemoryStore` and :class:`~evalroom.store.JsonlStore` are two small
implementations. Write your own against Postgres, SQLite or an object store if you need one.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from ..calibration import CalibrationVersion
from ..evalrun import EvalRun
from ..golden import GoldenCase
from ..review import Item, Review

__all__ = [
    "CalibrationStore",
    "DuplicateIdError",
    "EvalStore",
    "matches_review",
    "matches_tenant",
]

T = TypeVar("T")


class DuplicateIdError(KeyError):
    """Raised when a record with the same id already exists. Stores are append-only."""

    kind: str
    """Kind of record, e.g. ``"review"``."""
    id: str
    """The duplicated id."""

    def __init__(self, kind: str, id: str) -> None:
        super().__init__(f'{kind} "{id}" already exists')
        self.kind = kind
        self.id = id

    def __str__(self) -> str:
        return f'{self.kind} "{self.id}" already exists'


class EvalStore(Protocol):
    """Persistence for items, reviews, golden cases and runs.

    Every ``add_*`` is insert-only and rejects an existing id with :class:`DuplicateIdError`:
    reviews supersede each other by ``created_at``, never by overwriting. Listings return
    records in insertion order.
    """

    def add_item(self, item: Item) -> None: ...
    def get_item(self, id: str) -> Item | None: ...
    def list_items(self, *, tenant: str | None = None) -> list[Item]: ...
    def add_review(self, review: Review) -> None: ...
    def list_reviews(
        self,
        *,
        item_id: str | None = None,
        lens: str | None = None,
        rubric_version: str | None = None,
        tenant: str | None = None,
    ) -> list[Review]: ...
    def add_golden(self, golden: GoldenCase) -> None: ...
    def get_golden(self, id: str) -> GoldenCase | None: ...
    def list_golden(self, *, tenant: str | None = None) -> list[GoldenCase]: ...
    def add_run(self, run: EvalRun) -> None: ...
    def get_run(self, id: str) -> EvalRun | None: ...
    def list_runs(self, *, tenant: str | None = None) -> list[EvalRun]: ...


class CalibrationStore(Protocol[T]):
    """Persistence for calibration versions.

    ``save_version`` upserts by id (status changes are saves of the same id);
    ``list_versions`` returns the current state of each version, ordered by ``version``.
    Implementations are not required to be transactional: run one writer.
    """

    def list_versions(self) -> list[CalibrationVersion[T]]: ...
    def get_version(self, id: str) -> CalibrationVersion[T] | None: ...
    def save_version(self, version: CalibrationVersion[T]) -> None: ...


def matches_tenant(tenant_of_record: str | None, tenant: str | None) -> bool:
    """``True`` when the record passes a tenant filter (``None`` matches everything)."""
    return tenant is None or tenant_of_record == tenant


def matches_review(
    review: Review,
    *,
    item_id: str | None = None,
    lens: str | None = None,
    rubric_version: str | None = None,
    tenant: str | None = None,
) -> bool:
    """``True`` when ``review`` passes the filter."""
    return (
        (item_id is None or review.item_id == item_id)
        and (lens is None or review.lens == lens)
        and (rubric_version is None or review.rubric_version == rubric_version)
        and (tenant is None or review.tenant == tenant)
    )
