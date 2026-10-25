from __future__ import annotations

import json
from typing import Any

import pytest

from evalroom.audit import MemoryAuditSink
from evalroom.calibration import (
    activate,
    active_version,
    create_draft,
    diff_values,
    rollback,
    snapshot,
)
from evalroom.result import Err, Ok
from evalroom.store import MemoryStore

Config = dict[str, Any]


def seeded() -> tuple[MemoryStore[Config], MemoryAuditSink]:
    store: MemoryStore[Config] = MemoryStore()
    audit = MemoryAuditSink()
    v1 = create_draft(
        store,
        config={"model": "m-1", "temperature": 0.2},
        reason="initial",
        author="ana",
        at="2026-10-01T00:00:00.000Z",
        audit=audit,
    )
    assert isinstance(v1, Ok)
    a1 = activate(
        store,
        id=v1.value.id,
        actor="ben",
        reason="approved",
        at="2026-10-02T00:00:00.000Z",
        audit=audit,
    )
    assert isinstance(a1, Ok)
    return store, audit


def test_snapshot_copies_sorts_and_detaches() -> None:
    source: dict[str, Any] = {"b": 1, "a": {"d": [1, 2], "c": "x"}}
    snap = snapshot(source)
    assert json.dumps(snap, separators=(",", ":")) == '{"a":{"c":"x","d":[1,2]},"b":1}'
    source["a"]["c"] = "changed"
    assert snap["a"]["c"] == "x"


def test_snapshot_rejects_non_json() -> None:
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    with pytest.raises(TypeError, match="circular"):
        snapshot(cyclic)
    with pytest.raises(TypeError):
        snapshot({"f": lambda: 1})
    with pytest.raises(TypeError):
        snapshot({"when": object()})
    with pytest.raises(TypeError, match="finite"):
        snapshot({"n": float("nan")})


def test_diff_values_one_line_per_changed_leaf() -> None:
    assert diff_values(
        {"model": "m-1", "temperature": 0.2, "retry": {"max": 3}},
        {"model": "m-2", "temperature": 0.2, "retry": {"max": 5}, "stop": ["\n"]},
    ) == ['model: "m-1" → "m-2"', "retry.max: 3 → 5", 'stop: (absent) → ["\\n"]']


def test_diff_values_equal_and_whole_arrays() -> None:
    assert diff_values({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}) == []
    assert diff_values({"a": [1, 2]}, {"a": [2, 1]}) == ["a: [1,2] → [2,1]"]
    assert diff_values(1, 2) == ["(root): 1 → 2"]


def test_lifecycle_four_eyes_and_supersede() -> None:
    store, audit = seeded()
    draft = create_draft(
        store,
        config={"model": "m-2", "temperature": 0.2},
        reason="try m-2",
        author="ana",
        audit=audit,
    )
    assert isinstance(draft, Ok)
    assert (
        draft.value.id,
        draft.value.version,
        draft.value.status,
        draft.value.base_version_id,
    ) == (
        "cal-2",
        2,
        "draft",
        "cal-1",
    )
    own = activate(store, id="cal-2", actor="ana", reason="ship it", audit=audit)
    assert isinstance(own, Err)
    assert own.error.code == "SELF_ACTIVATION"
    done = activate(store, id="cal-2", actor="ben", reason="kappa held", audit=audit)
    assert isinstance(done, Ok)
    assert done.value.status == "active"
    assert [(v.id, v.status) for v in store.list_versions()] == [
        ("cal-1", "superseded"),
        ("cal-2", "active"),
    ]
    assert [e.action for e in audit.list()] == [
        "calibration.draft",
        "calibration.activate",
        "calibration.draft",
        "calibration.activate",
    ]


def test_activation_refusals() -> None:
    store, _ = seeded()

    def code_of(result: Any) -> str:
        assert isinstance(result, Err)
        return str(result.error.code)

    assert code_of(activate(store, id="nope", actor="ben", reason="x")) == "NOT_FOUND"
    assert code_of(activate(store, id="cal-1", actor="ben", reason="x")) == "NOT_DRAFT"
    assert code_of(activate(store, id="cal-1", actor="ben", reason=" ")) == "REASON_REQUIRED"
    assert (
        code_of(create_draft(store, config={"model": "x"}, reason="", author="a"))
        == "REASON_REQUIRED"
    )
    assert code_of(create_draft(store, config={}, reason="r", author=" ")) == "ACTOR_REQUIRED"


def test_rollback_restores_the_latest_superseded_version() -> None:
    store, audit = seeded()
    for model in ("m-2", "m-3"):
        draft = create_draft(
            store, config={"model": model, "temperature": 0.2}, reason=model, author="ana"
        )
        assert isinstance(draft, Ok)
        activate(store, id=draft.value.id, actor="ben", reason="go")
    restored = rollback(store, actor="ben", reason="m-3 regressed on tone", audit=audit)
    assert isinstance(restored, Ok)
    assert restored.value.id == "cal-2"
    current = active_version(store)
    assert current is not None
    assert current.config["model"] == "m-2"
    assert [v.status for v in store.list_versions()] == ["superseded", "active", "rolled_back"]
    last = audit.list(action="calibration.rollback")[0]
    assert (last.actor, last.target, last.reason) == ("ben", "cal-2", "m-3 regressed on tone")


def test_rollback_with_nothing_to_restore() -> None:
    empty: MemoryStore[Config] = MemoryStore()
    nothing = rollback(empty, actor="ben", reason="x")
    assert isinstance(nothing, Err)
    assert nothing.error.code == "NO_ACTIVE"
    store, _ = seeded()
    no_previous = rollback(store, actor="ben", reason="x")
    assert isinstance(no_previous, Err)
    assert no_previous.error.code == "NO_PREVIOUS"
