import { beforeEach, describe, expect, it } from "vitest";
import { agreementReport } from "../src/agreement";
import { defineRubric } from "../src/rubric";
import { resetClock, review, rubric, triple } from "./fixtures";

beforeEach(resetClock);

describe("agreementReport", () => {
  it("counts only complete items and uses the latest verdict per lens", () => {
    const report = agreementReport(
      [
        review("i1", "accuracy", "FAIL"),
        ...triple("i1", "PASS", "PASS", "PASS"), // later rows overrule the early FAIL
        review("i2", "accuracy", "PASS"),
        review("i2", "tone", "PASS"),
      ],
      rubric,
    );
    expect(report.items).toBe(1);
    expect(report.incompleteItems).toBe(1);
    expect(report.rawAgreement).toMatchObject({ p: 1, k: 1, n: 1, decidable: false });
    expect(report.prevalence.PASS).toMatchObject({ p: 1, k: 3, n: 3 });
    expect(report.prevalence.FAIL).toMatchObject({ p: 0, k: 0, n: 3 });
  });

  it("computes agreement within one rubric version only", () => {
    const rows = [
      ...triple("i1", "PASS", "PASS", "PASS", { rubricVersion: "v0" }),
      ...triple("i2", "PASS", "FAIL", "PASS"),
    ];
    const current = agreementReport(rows, rubric);
    expect(current.rubricVersion).toBe("v1");
    expect(current.items).toBe(1);
    expect(current.excludedReviews).toBe(3);
    const older = agreementReport(rows, rubric, { rubricVersion: "v0" });
    expect(older.items).toBe(1);
    expect(older.disagreements).toHaveLength(0);
  });

  it("returns null kappa with a reason below minItems and a value from minItems", () => {
    const nine = Array.from({ length: 9 }, (_, i) =>
      triple(`i${i}`, "PASS", "PASS", i % 2 ? "FAIL" : "PASS"),
    ).flat();
    const small = agreementReport(nine, rubric);
    expect(small.fleissKappa).toEqual({
      value: null,
      band: null,
      n: 9,
      reason: "9 complete items; kappa needs at least 10",
    });
    expect(small.pairs[0]?.cohenKappa.value).toBeNull();
    expect(small.interpretation).toBe(
      "Kappa not reported: 9 complete items; kappa needs at least 10.",
    );

    const ten = Array.from({ length: 10 }, (_, i) => {
      const v = i % 2 ? "FAIL" : "PASS";
      return triple(`i${i}`, v, v, v);
    }).flat();
    const enough = agreementReport(ten, rubric);
    expect(enough.fleissKappa.value).toBeCloseTo(1, 10);
    expect(enough.fleissKappa.band).toBe("almost perfect");
    expect(enough.pairs.every((pair) => pair.cohenKappa.value === 1)).toBe(true);
  });

  it("explains an undefined kappa when every verdict is identical", () => {
    const rows = Array.from({ length: 12 }, (_, i) =>
      triple(`i${i}`, "PASS", "PASS", "PASS"),
    ).flat();
    const report = agreementReport(rows, rubric);
    expect(report.fleissKappa.value).toBeNull();
    expect(report.fleissKappa.reason).toContain("undefined");
    expect(report.rawAgreement.p).toBe(1);
  });

  it("matches a hand-computed kappa on a mixed table and flags a dominant verdict", () => {
    // 10 items, 3 lenses. 6 unanimous PASS, 2 unanimous FAIL, 2 split 2 PASS / 1 FAIL.
    // P̄ = (8·1 + 2·(1/3)) / 10 = 0.86667; p_PASS = 22/30, p_FAIL = 8/30;
    // P_e = 0.53778 + 0.07111 = 0.60889; κ = (0.86667 - 0.60889) / 0.39111 = 0.65909.
    const rows = [
      ...Array.from({ length: 6 }, (_, i) => triple(`p${i}`, "PASS", "PASS", "PASS")).flat(),
      ...triple("f1", "FAIL", "FAIL", "FAIL"),
      ...triple("f2", "FAIL", "FAIL", "FAIL"),
      ...triple("s1", "PASS", "FAIL", "PASS"),
      ...triple("s2", "PASS", "PASS", "FAIL"),
    ];
    const report = agreementReport(rows, rubric);
    expect(report.fleissKappa.value).toBeCloseTo(0.65909, 4);
    expect(report.fleissKappa.band).toBe("substantial");
    expect(report.rawAgreement).toMatchObject({ k: 8, n: 10 });
    expect(report.disagreements.map((d) => d.itemId)).toEqual(["s1", "s2"]);
    expect(report.disagreements[0]?.verdicts[1]).toMatchObject({
      lens: "tone",
      verdict: "FAIL",
      errorClass: "DISMISSIVE",
    });
  });

  it("reports lens-pair agreement, confusion and median seconds per lens", () => {
    const report = agreementReport(
      [
        ...triple("i1", "PASS", "PASS", "FAIL", { seconds: 60 }),
        ...triple("i2", "PASS", "FAIL", "FAIL", { seconds: 180 }),
      ],
      rubric,
    );
    expect(
      report.pairs.map((pair) => [pair.a, pair.b, pair.agreement.k, pair.agreement.n]),
    ).toEqual([
      ["accuracy", "tone", 1, 2],
      ["accuracy", "policy", 0, 2],
      ["tone", "policy", 1, 2],
    ]);
    expect(report.pairs[1]?.confusion.PASS.FAIL).toBe(2);
    expect(report.medianSeconds).toBe(120);
    expect(report.medianSecondsByLens).toEqual({ accuracy: 120, tone: 120, policy: 120 });
  });

  it("refuses kappa for a single-lens rubric", () => {
    const solo = defineRubric({ version: "v1", lenses: ["accuracy"], errorClasses: [] });
    const rows = Array.from({ length: 12 }, (_, i) => review(`i${i}`, "accuracy", "PASS"));
    const report = agreementReport(rows, solo);
    expect(report.fleissKappa.reason).toBe("kappa needs at least two lenses");
    expect(report.pairs).toEqual([]);
  });
});
