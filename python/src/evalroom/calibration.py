"""Calibration: versioned configuration under test (prompt, model, thresholds, rule sets).

A change is a draft with a reason; activating it supersedes the active version; rolling back
restores the most recent superseded one. Each step can write an audit event.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar

from ._js import stringify, utf16_truncate
from ._serde import MISSING, optional, to_json
from ._time import now_iso
from .audit import AuditEvent, AuditSink
from .result import Result, err, is_record, ok
from .rules import deep_equal

if TYPE_CHECKING:
    from .store.types import CalibrationStore

__all__ = [
    "CalibrationRefusal",
    "CalibrationRefusalCode",
    "CalibrationStatus",
    "CalibrationVersion",
    "activate",
    "active_version",
    "calibration_version_from_dict",
    "create_draft",
    "diff_values",
    "rollback",
    "snapshot",
]

T = TypeVar("T")

CalibrationStatus = Literal["draft", "active", "superseded", "rolled_back"]
"""Lifecycle of a calibration version."""


@dataclass(frozen=True, slots=True)
class CalibrationVersion(Generic[T]):
    """One version of a configuration."""

    id: str
    """Version id."""
    version: int
    """Monotonic version number, starting at 1."""
    status: CalibrationStatus
    """Current status."""
    config: T
    """The configuration itself, as a :func:`snapshot`."""
    reason: str
    """Why this version exists."""
    author: str
    """Who drafted it."""
    created_at: str
    """ISO-8601 creation time."""
    base_version_id: str | None
    """The version that was active when this draft was created, or ``None``."""
    activated_at: str | None = optional()
    """When it was activated, if ever."""
    activated_by: str | None = optional()
    """Who activated it, if ever."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of one ``calibration.jsonl`` line."""
        result: dict[str, Any] = to_json(self)
        return result


def calibration_version_from_dict(data: object) -> CalibrationVersion[Any]:
    """Reads one ``calibration.jsonl`` line."""
    if not is_record(data):
        raise ValueError("a calibration version must be an object")
    return CalibrationVersion(
        id=data["id"],
        version=data["version"],
        status=data["status"],
        config=data.get("config"),
        reason=data.get("reason", ""),
        author=data.get("author", ""),
        created_at=data.get("createdAt", ""),
        base_version_id=data.get("baseVersionId"),
        activated_at=data.get("activatedAt"),
        activated_by=data.get("activatedBy"),
    )


CalibrationRefusalCode = Literal[
    "REASON_REQUIRED",
    "ACTOR_REQUIRED",
    "NOT_FOUND",
    "NOT_DRAFT",
    "SELF_ACTIVATION",
    "NO_ACTIVE",
    "NO_PREVIOUS",
]


@dataclass(frozen=True, slots=True)
class CalibrationRefusal:
    """Why a calibration step was refused."""

    code: CalibrationRefusalCode
    """Machine code."""
    message: str
    """Human-readable explanation."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape."""
        return {"code": self.code, "message": self.message}


def _sort_keys(value: Any, seen: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise TypeError("snapshot: numbers must be finite")
        return value
    if isinstance(value, (list, tuple, dict)):
        if id(value) in seen:
            raise TypeError("snapshot: circular reference")
        seen.add(id(value))
        out: Any
        if isinstance(value, dict):
            if not all(isinstance(key, str) for key in value):
                raise TypeError("snapshot: object keys must be strings")
            out = {
                key: _sort_keys(value[key], seen)
                for key in sorted(value)
                if value[key] is not MISSING
            }
        else:
            out = [_sort_keys(item, seen) for item in value]
        seen.discard(id(value))
        return out
    if callable(value):
        raise TypeError(f"snapshot: {type(value).__name__} is not JSON-serialisable")
    raise TypeError("snapshot: only plain objects, arrays and JSON primitives are allowed")


def snapshot(config: T) -> T:
    """A deep, key-sorted copy of a JSON-like configuration.

    Two equal configs produce snapshots that serialise identically. (Python cannot freeze a
    dict, so the snapshot is a fresh copy rather than a frozen one.)

    :raises TypeError: for values that are not plain JSON (functions, class instances, cycles,
        non-finite numbers).
    """
    result: T = _sort_keys(config, set())
    return result


def _format_value(value: Any) -> str:
    if value is MISSING:
        return "(absent)"
    return utf16_truncate(stringify(value), 60, 57)


def diff_values(before: Any, after: Any, path: str = "") -> list[str]:
    """Human-readable lines describing how ``after`` differs from ``before``.

    One line per changed leaf: ``'retry.max: 3 → 5'``, ``'model: "a" → "b"'``,
    ``'stop: (absent) → ["\\n"]'``. Objects are walked key by key; lists and primitives are
    compared whole. Returns ``[]`` when the values are structurally equal.
    """
    if is_record(before) and is_record(after):
        keys = sorted(set(before) | set(after))
        lines: list[str] = []
        for key in keys:
            lines.extend(
                diff_values(
                    before.get(key, MISSING),
                    after.get(key, MISSING),
                    f"{path}.{key}" if path else key,
                )
            )
        return lines
    if deep_equal(before, after):
        return []
    return [f"{path or '(root)'}: {_format_value(before)} → {_format_value(after)}"]


def active_version(store: CalibrationStore[T]) -> CalibrationVersion[T] | None:
    """The active version, if any."""
    return next((v for v in store.list_versions() if v.status == "active"), None)


def _refuse(code: CalibrationRefusalCode, message: str) -> Result[Any, CalibrationRefusal]:
    return err(CalibrationRefusal(code=code, message=message))


def create_draft(
    store: CalibrationStore[T],
    *,
    config: T,
    reason: str,
    author: str,
    id: str | None = None,
    at: str | None = None,
    audit: AuditSink | None = None,
) -> Result[CalibrationVersion[T], CalibrationRefusal]:
    """Stores a new draft version.

    The draft records the currently active version as its base so the change can be reviewed
    with ``diff_values(base.config, draft.config)``.
    """
    reason = reason.strip()
    if not reason:
        return _refuse("REASON_REQUIRED", "a draft needs a reason")
    if not author.strip():
        return _refuse("ACTOR_REQUIRED", "a draft needs an author")
    versions = store.list_versions()
    nxt = max((v.version for v in versions), default=0) + 1
    active = next((v for v in versions if v.status == "active"), None)
    draft = CalibrationVersion(
        id=id if id is not None else f"cal-{nxt}",
        version=nxt,
        status="draft",
        config=snapshot(config),
        reason=reason,
        author=author,
        created_at=at if at is not None else now_iso(),
        base_version_id=active.id if active else None,
    )
    store.save_version(draft)
    if audit is not None:
        audit.append(
            AuditEvent(
                at=draft.created_at,
                actor=draft.author,
                action="calibration.draft",
                target=draft.id,
                reason=reason,
                after={
                    "version": draft.version,
                    "status": "draft",
                    "baseVersionId": draft.base_version_id,
                },
            )
        )
    return ok(draft)


def activate(
    store: CalibrationStore[T],
    *,
    id: str,
    actor: str,
    reason: str,
    allow_self_activation: bool = False,
    at: str | None = None,
    audit: AuditSink | None = None,
) -> Result[CalibrationVersion[T], CalibrationRefusal]:
    """Activates a draft and supersedes the previously active version.

    By default the author may not activate their own draft (four-eyes).
    """
    reason = reason.strip()
    if not reason:
        return _refuse("REASON_REQUIRED", "activation needs a reason")
    if not actor.strip():
        return _refuse("ACTOR_REQUIRED", "activation needs an actor")
    target = store.get_version(id)
    if target is None:
        return _refuse("NOT_FOUND", f"no calibration version {id}")
    if target.status != "draft":
        return _refuse(
            "NOT_DRAFT", f"only a draft can be activated; {target.id} is {target.status}"
        )
    if target.author == actor and not allow_self_activation:
        return _refuse("SELF_ACTIVATION", "the activator must differ from the author")
    when = at if at is not None else now_iso()
    previous = active_version(store)
    if previous is not None:
        store.save_version(dataclasses.replace(previous, status="superseded"))
    activated = dataclasses.replace(target, status="active", activated_at=when, activated_by=actor)
    store.save_version(activated)
    if audit is not None:
        audit.append(
            AuditEvent(
                at=when,
                actor=actor,
                action="calibration.activate",
                target=activated.id,
                reason=reason,
                before={
                    "status": "draft",
                    "activeVersionId": previous.id if previous else None,
                },
                after={"status": "active", "version": activated.version},
            )
        )
    return ok(activated)


def rollback(
    store: CalibrationStore[T],
    *,
    actor: str,
    reason: str,
    at: str | None = None,
    audit: AuditSink | None = None,
) -> Result[CalibrationVersion[T], CalibrationRefusal]:
    """Marks the active version ``rolled_back`` and re-activates the latest superseded one."""
    reason = reason.strip()
    if not reason:
        return _refuse("REASON_REQUIRED", "a rollback needs a reason")
    if not actor.strip():
        return _refuse("ACTOR_REQUIRED", "a rollback needs an actor")
    versions = store.list_versions()
    current = next((v for v in versions if v.status == "active"), None)
    if current is None:
        return _refuse("NO_ACTIVE", "there is no active version to roll back")
    superseded = sorted(
        (v for v in versions if v.status == "superseded"), key=lambda v: v.version, reverse=True
    )
    if not superseded:
        return _refuse("NO_PREVIOUS", "there is no superseded version to restore")
    previous = superseded[0]
    when = at if at is not None else now_iso()
    store.save_version(dataclasses.replace(current, status="rolled_back"))
    restored = dataclasses.replace(previous, status="active", activated_at=when, activated_by=actor)
    store.save_version(restored)
    if audit is not None:
        audit.append(
            AuditEvent(
                at=when,
                actor=actor,
                action="calibration.rollback",
                target=restored.id,
                reason=reason,
                before={"activeVersionId": current.id, "version": current.version},
                after={"activeVersionId": restored.id, "version": restored.version},
            )
        )
    return ok(restored)
