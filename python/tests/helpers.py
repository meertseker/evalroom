"""Shared test data, mirroring typescript/test/fixtures.ts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evalroom.review import Review
from evalroom.rubric import ErrorClassDef, Verdict, define_rubric

ROOT = Path(__file__).resolve().parents[2]
CONFORMANCE = ROOT / "conformance"
DATA = CONFORMANCE / "data"

rubric = define_rubric(
    version="v1",
    lenses=["accuracy", "tone", "policy"],
    error_classes=[
        ErrorClassDef(
            code="WRONG_FACT",
            label="Wrong fact",
            lenses=("accuracy",),
            include="States something false about the order, account or product.",
            exclude="Incomplete but not false: pick MISSED_QUESTION.",
        ),
        ErrorClassDef(
            code="DISMISSIVE",
            label="Dismissive tone",
            lenses=("tone",),
            include="Blames the customer or brushes them off.",
            exclude="Firm but polite refusals are fine.",
        ),
        ErrorClassDef(
            code="UNSUPPORTED_PROMISE",
            label="Unsupported promise",
            lenses=("policy", "accuracy"),
            include="Promises a refund, credit or date the policy does not allow.",
            exclude="Quoting the policy correctly is fine.",
        ),
    ],
)


class Clock:
    """Monotonic review factory: ``r1``, ``r2``, ... one second apart."""

    def __init__(self) -> None:
        self.tick = 0

    def review(self, item_id: str, lens: str, verdict: Verdict, **extra: Any) -> Review:
        self.tick += 1
        at = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(seconds=self.tick)
        base = Review(
            id=f"r{self.tick}",
            item_id=item_id,
            lens=lens,
            verdict=verdict,
            reviewer=f"{lens}-reviewer",
            rubric_version="v1",
            created_at=at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
        if verdict == "FAIL":
            code = {"tone": "DISMISSIVE", "policy": "UNSUPPORTED_PROMISE"}.get(lens, "WRONG_FACT")
            base = replace(base, error_class=code, severity="medium", note="fails")
        return replace(base, **extra)

    def triple(
        self, item_id: str, accuracy: Verdict, tone: Verdict, policy: Verdict, **extra: Any
    ) -> list[Review]:
        return [
            self.review(item_id, "accuracy", accuracy, **extra),
            self.review(item_id, "tone", tone, **extra),
            self.review(item_id, "policy", policy, **extra),
        ]
