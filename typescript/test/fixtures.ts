import type { Review } from "../src/review";
import { defineRubric, type Verdict } from "../src/rubric";

export const rubric = defineRubric({
  version: "v1",
  lenses: ["accuracy", "tone", "policy"],
  errorClasses: [
    {
      code: "WRONG_FACT",
      label: "Wrong fact",
      lenses: ["accuracy"],
      include: "States something false about the order, account or product.",
      exclude: "Incomplete but not false: pick MISSED_QUESTION.",
    },
    {
      code: "DISMISSIVE",
      label: "Dismissive tone",
      lenses: ["tone"],
      include: "Blames the customer or brushes them off.",
      exclude: "Firm but polite refusals are fine.",
    },
    {
      code: "UNSUPPORTED_PROMISE",
      label: "Unsupported promise",
      lenses: ["policy", "accuracy"],
      include: "Promises a refund, credit or date the policy does not allow.",
      exclude: "Quoting the policy correctly is fine.",
    },
  ],
});

let clock = 0;

/** Resets the monotonic test clock. */
export function resetClock(): void {
  clock = 0;
}

/** Builds a review with a strictly increasing createdAt. */
export function review(
  itemId: string,
  lens: string,
  verdict: Verdict,
  extra: Partial<Review> = {},
): Review {
  clock += 1;
  const base: Review = {
    id: `r${clock}`,
    itemId,
    lens,
    verdict,
    reviewer: `${lens}-reviewer`,
    rubricVersion: "v1",
    createdAt: new Date(Date.UTC(2026, 9, 1, 0, 0, clock)).toISOString(),
  };
  if (verdict === "FAIL") {
    base.errorClass =
      lens === "tone" ? "DISMISSIVE" : lens === "policy" ? "UNSUPPORTED_PROMISE" : "WRONG_FACT";
    base.severity = "medium";
    base.note = "fails";
  }
  return { ...base, ...extra };
}

/** Three reviews, one per lens, in accuracy/tone/policy order. */
export function triple(
  itemId: string,
  accuracy: Verdict,
  tone: Verdict,
  policy: Verdict,
  extra: Partial<Review> = {},
): Review[] {
  return [
    review(itemId, "accuracy", accuracy, extra),
    review(itemId, "tone", tone, extra),
    review(itemId, "policy", policy, extra),
  ];
}
