"""Append-only audit trail.

Golden promotion and calibration draft, activation and rollback each write one
:class:`AuditEvent`.
"""

from __future__ import annotations

import builtins
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ._serde import optional, to_json
from .jsonl import append_jsonl, read_jsonl
from .result import is_record

__all__ = ["AuditEvent", "AuditSink", "JsonlAuditSink", "MemoryAuditSink", "audit_event_from_dict"]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One immutable audit record."""

    at: str
    """ISO-8601 timestamp."""
    actor: str
    """Who did it."""
    action: str
    """What was done, e.g. ``"golden.promote"``, ``"calibration.activate"``."""
    target: str
    """Id of what it was done to."""
    reason: str | None = optional()
    """Why, when the caller gave a reason."""
    before: Any = optional()
    """State before, when meaningful."""
    after: Any = optional()
    """State after, when meaningful."""
    tenant: str | None = optional()
    """Optional tenant tag."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of one ``audit.jsonl`` line."""
        result: dict[str, Any] = to_json(self)
        return result


def audit_event_from_dict(data: object) -> AuditEvent:
    """Reads one audit line. Unknown keys are ignored.

    :raises ValueError: when a required field is missing.
    """
    if not is_record(data):
        raise ValueError("an audit event must be an object")
    for key in ("at", "actor", "action", "target"):
        if not isinstance(data.get(key), str):
            raise ValueError(f"an audit event needs a string {key}")
    return AuditEvent(
        at=data["at"],
        actor=data["actor"],
        action=data["action"],
        target=data["target"],
        reason=data.get("reason"),
        before=data.get("before"),
        after=data.get("after"),
        tenant=data.get("tenant"),
    )


def _matches(
    event: AuditEvent,
    action: str | None,
    target: str | None,
    actor: str | None,
    tenant: str | None,
) -> bool:
    return (
        (action is None or event.action == action)
        and (target is None or event.target == target)
        and (actor is None or event.actor == actor)
        and (tenant is None or event.tenant == tenant)
    )


class AuditSink(Protocol):
    """Where audit events go. Implementations must be append-only."""

    def append(self, event: AuditEvent) -> None:
        """Appends one event."""

    def list(
        self,
        *,
        action: str | None = None,
        target: str | None = None,
        actor: str | None = None,
        tenant: str | None = None,
    ) -> builtins.list[AuditEvent]:
        """Lists events in append order, optionally filtered."""


class MemoryAuditSink:
    """In-memory :class:`AuditSink`. Events are deep-copied on the way in and out."""

    def __init__(self) -> None:
        self._events: builtins.list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        """Appends one event."""
        self._events.append(copy.deepcopy(event))

    def list(
        self,
        *,
        action: str | None = None,
        target: str | None = None,
        actor: str | None = None,
        tenant: str | None = None,
    ) -> builtins.list[AuditEvent]:
        """Lists events in append order, optionally filtered."""
        return [
            copy.deepcopy(event)
            for event in self._events
            if _matches(event, action, target, actor, tenant)
        ]


class JsonlAuditSink:
    """File-backed :class:`AuditSink`: one JSON object per line, appended, never rewritten."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        """Path of the ``.jsonl`` file. Created on first append."""

    def append(self, event: AuditEvent) -> None:
        """Appends one event."""
        append_jsonl(self.path, [event.to_dict()])

    def list(
        self,
        *,
        action: str | None = None,
        target: str | None = None,
        actor: str | None = None,
        tenant: str | None = None,
    ) -> builtins.list[AuditEvent]:
        """Lists events in append order, optionally filtered."""
        events = [audit_event_from_dict(raw) for raw in read_jsonl(self.path, missing_ok=True)]
        return [event for event in events if _matches(event, action, target, actor, tenant)]
