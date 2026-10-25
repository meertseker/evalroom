import { beforeEach, describe, expect, it } from "vitest";
import { MemoryAuditSink } from "../src/audit";
import { type PromoteOptions, promoteAndRecord, promoteToGolden } from "../src/golden";
import type { Item } from "../src/review";
import { resetClock, review, rubric, triple } from "./fixtures";

const item: Item = {
  id: "t-1",
  input: { message: "Where is my refund?" },
  output: { reply: "Your refund was issued on 3 October and takes 5–7 business days." },
  tenant: "acme",
};

const options: PromoteOptions = {
  rubric,
  expectedRules: [{ path: "reply", op: "notContains", value: ["guarantee"], code: "PROMISE" }],
  promotedBy: "maintainer",
  promotedAt: "2026-10-20T10:00:00.000Z",
};

beforeEach(resetClock);

function codes(result: ReturnType<typeof promoteToGolden>): string[] {
  return result.ok ? [] : result.error.map((refusal) => refusal.code);
}

describe("promoteToGolden", () => {
  it("promotes a unanimously passed item and freezes input, output and provenance", () => {
    const reviews = triple("t-1", "PASS", "PASS", "PASS");
    const result = promoteToGolden(item, reviews, options);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.value).toEqual({
      id: "golden-t-1",
      itemId: "t-1",
      input: item.input,
      referenceOutput: item.output,
      expectedRules: options.expectedRules,
      provenance: {
        reviewIds: ["r1", "r2", "r3"],
        promotedBy: "maintainer",
        promotedAt: options.promotedAt,
      },
      rubricVersion: "v1",
      tenant: "acme",
    });
    expect(result.value.input).not.toBe(item.input);
  });

  it("refuses and names the missing lens", () => {
    const result = promoteToGolden(
      item,
      [review("t-1", "accuracy", "PASS"), review("t-1", "tone", "PASS")],
      options,
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error[0]).toMatchObject({ code: "MISSING_LENSES", lenses: ["policy"] });
    }
  });

  it("refuses a split verdict by default and allows it when unanimity is not required", () => {
    const reviews = triple("t-1", "PASS", "NEEDS_REVIEW", "PASS");
    const refused = promoteToGolden(item, reviews, options);
    expect(codes(refused)).toEqual(["NOT_UNANIMOUS_PASS"]);
    if (!refused.ok)
      expect(refused.error[0]?.message).toBe("not every lens passed: tone=NEEDS_REVIEW");
    expect(promoteToGolden(item, reviews, { ...options, requireUnanimousPass: false }).ok).toBe(
      true,
    );
  });

  it("uses the latest verdict per lens and ignores other items and rubric versions", () => {
    const reviews = [
      ...triple("t-1", "PASS", "FAIL", "PASS"),
      review("t-1", "tone", "PASS"),
      review("t-2", "tone", "FAIL"),
      review("t-1", "policy", "FAIL", { rubricVersion: "v0" }),
    ];
    const result = promoteToGolden(item, reviews, options);
    expect(result.ok && result.value.provenance.reviewIds).toEqual(["r1", "r4", "r3"]);
  });

  it("does not count reviews written against an older rubric", () => {
    const reviews = triple("t-1", "PASS", "PASS", "PASS", { rubricVersion: "v0" });
    expect(codes(promoteToGolden(item, reviews, options))).toEqual(["MISSING_LENSES"]);
  });

  it("collects every refusal at once: rules, promoter, lenses", () => {
    const result = promoteToGolden(item, [], { ...options, expectedRules: [], promotedBy: " " });
    expect(codes(result)).toEqual(["MISSING_LENSES", "NO_RULES", "NO_PROMOTER"]);
    const invalid = promoteToGolden(item, triple("t-1", "PASS", "PASS", "PASS"), {
      ...options,
      expectedRules: [{ path: "reply", op: "between", value: "x" }],
    });
    expect(codes(invalid)).toEqual(["INVALID_RULES"]);
  });

  it("refuses rules that reject the output every reviewer passed", () => {
    const result = promoteToGolden(item, triple("t-1", "PASS", "PASS", "PASS"), {
      ...options,
      expectedRules: [{ path: "reply", op: "contains", value: "30 days", code: "WINDOW" }],
    });
    expect(codes(result)).toEqual(["REFERENCE_OUTPUT_FAILS"]);
    if (!result.ok) expect(result.error[0]?.message).toContain("WINDOW");
  });
});

describe("promoteAndRecord", () => {
  it("writes one audit event on success and none on refusal", async () => {
    const audit = new MemoryAuditSink();
    await promoteAndRecord(item, [], options, audit);
    expect(await audit.list()).toEqual([]);
    await promoteAndRecord(item, triple("t-1", "PASS", "PASS", "PASS"), options, audit);
    const events = await audit.list();
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      action: "golden.promote",
      actor: "maintainer",
      target: "golden-t-1",
      at: options.promotedAt,
      tenant: "acme",
    });
  });
});
