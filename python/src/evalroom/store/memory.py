"""In-memory implementation of :class:`EvalStore` and :class:`CalibrationStore`.

Records are deep-copied on the way in and out, so callers cannot mutate stored state.
"""

from __future__ import annotations

import copy
from typing import Generic, TypeVar

from ..calibration import CalibrationVersion
from ..evalrun import EvalRun
from ..golden import GoldenCase
from ..review import Item, Review
from .types import DuplicateIdError, matches_review, matches_tenant

__all__ = ["MemoryStore"]

T = TypeVar("T")
V = TypeVar("V", Item, Review, GoldenCase, EvalRun)


def _insert(table: dict[str, V], kind: str, value: V) -> None:
    if value.id in table:
        raise DuplicateIdError(kind, value.id)
    table[value.id] = copy.deepcopy(value)


class MemoryStore(Generic[T]):
    """A store that lives in process memory. Good for tests, notebooks and short scripts."""

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._reviews: dict[str, Review] = {}
        self._golden: dict[str, GoldenCase] = {}
        self._runs: dict[str, EvalRun] = {}
        self._versions: dict[str, CalibrationVersion[T]] = {}

    def add_item(self, item: Item) -> None:
        """Adds an item."""
        _insert(self._items, "item", item)

    def get_item(self, id: str) -> Item | None:
        """The item with this id, if any."""
        return copy.deepcopy(self._items.get(id))

    def list_items(self, *, tenant: str | None = None) -> list[Item]:
        """All items, optionally for one tenant."""
        return [copy.deepcopy(i) for i in self._items.values() if matches_tenant(i.tenant, tenant)]

    def add_review(self, review: Review) -> None:
        """Adds a review."""
        _insert(self._reviews, "review", review)

    def list_reviews(
        self,
        *,
        item_id: str | None = None,
        lens: str | None = None,
        rubric_version: str | None = None,
        tenant: str | None = None,
    ) -> list[Review]:
        """Reviews matching the filter."""
        return [
            copy.deepcopy(review)
            for review in self._reviews.values()
            if matches_review(
                review, item_id=item_id, lens=lens, rubric_version=rubric_version, tenant=tenant
            )
        ]

    def add_golden(self, golden: GoldenCase) -> None:
        """Adds a golden case."""
        _insert(self._golden, "golden case", golden)

    def get_golden(self, id: str) -> GoldenCase | None:
        """The golden case with this id, if any."""
        return copy.deepcopy(self._golden.get(id))

    def list_golden(self, *, tenant: str | None = None) -> list[GoldenCase]:
        """All golden cases, optionally for one tenant."""
        return [copy.deepcopy(g) for g in self._golden.values() if matches_tenant(g.tenant, tenant)]

    def add_run(self, run: EvalRun) -> None:
        """Adds an eval run."""
        _insert(self._runs, "run", run)

    def get_run(self, id: str) -> EvalRun | None:
        """The run with this id, if any."""
        return copy.deepcopy(self._runs.get(id))

    def list_runs(self, *, tenant: str | None = None) -> list[EvalRun]:
        """All runs, optionally for one tenant."""
        return [copy.deepcopy(r) for r in self._runs.values() if matches_tenant(r.tenant, tenant)]

    def list_versions(self) -> list[CalibrationVersion[T]]:
        """Every calibration version, current state, ascending by ``version``."""
        return [copy.deepcopy(v) for v in sorted(self._versions.values(), key=lambda v: v.version)]

    def get_version(self, id: str) -> CalibrationVersion[T] | None:
        """The calibration version with this id, if any."""
        return copy.deepcopy(self._versions.get(id))

    def save_version(self, version: CalibrationVersion[T]) -> None:
        """Inserts or replaces the calibration version with this id."""
        self._versions[version.id] = copy.deepcopy(version)
