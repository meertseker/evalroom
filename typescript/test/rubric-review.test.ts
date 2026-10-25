import { beforeEach, describe, expect, it } from "vitest";
import {
  completeItems,
  incompleteItems,
  latestPerLens,
  missingLenses,
  normalizeSeconds,
  parseItem,
  validateReview,
} from "../src/review";
import { defineRubric, errorClassesForLens, parseRubric, RubricError } from "../src/rubric";
import { resetClock, review, rubric, triple } from "./fixtures";

beforeEach(resetClock);

describe("defineRubric", () => {
  it("returns a frozen rubric with literal lens types", () => {
    expect(rubric.version).toBe("v1");
    expect(Object.isFrozen(rubric)).toBe(true);
    expect(Object.isFrozen(rubric.lenses)).toBe(true);
    expect(errorClassesForLens(rubric, "accuracy").map((entry) => entry.code)).toEqual([
      "WRONG_FACT",
      "UNSUPPORTED_PROMISE",
    ]);
  });

  it("rejects duplicate codes, duplicate lenses and undeclared lens references, listing all", () => {
    const attempt = () =>
      defineRubric({
        version: "v1",
        lenses: ["a", "a"],
        errorClasses: [
          { code: "X", label: "x", lenses: ["a"], include: "i", exclude: "e" },
          { code: "X", label: "x", lenses: ["b" as "a"], include: "i", exclude: "e" },
        ],
      });
    expect(attempt).toThrow(RubricError);
    try {
      attempt();
    } catch (error) {
      const issues = (error as RubricError).issues;
      expect(issues).toContain('lens "a" is declared twice');
      expect(issues).toContain('error class code "X" is not unique');
      expect(issues.some((issue) => issue.includes('undeclared lens "b"'))).toBe(true);
    }
  });

  it("parseRubric returns a Result for untrusted JSON", () => {
    expect(parseRubric({ version: "", lenses: [], errorClasses: {} })).toEqual({
      ok: false,
      error: [
        "version must be a non-empty string",
        "lenses must be a non-empty array",
        "errorClasses must be an array",
      ],
    });
    const good = parseRubric({ version: "v2", lenses: ["x"], errorClasses: [] });
    expect(good.ok && good.value.lenses).toEqual(["x"]);
  });
});

describe("validateReview", () => {
  it("requires class, severity and note on a FAIL", () => {
    const result = validateReview({ ...review("i1", "accuracy", "PASS"), verdict: "FAIL" });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toEqual([
        "a FAIL requires errorClass",
        "a FAIL requires severity",
        "a FAIL requires a note",
      ]);
    }
    const relaxed = validateReview(
      { ...review("i1", "accuracy", "PASS"), verdict: "FAIL" },
      { requireFailDetails: false },
    );
    expect(relaxed.ok).toBe(true);
  });

  it("checks lens and error class against the rubric of the same version only", () => {
    const wrongLens = validateReview(review("i1", "tone", "FAIL", { errorClass: "WRONG_FACT" }), {
      rubric,
    });
    expect(wrongLens.ok).toBe(false);
    if (!wrongLens.ok) expect(wrongLens.error[0]).toContain('not available to lens "tone"');
    const unknownLens = validateReview(review("i1", "speed", "PASS"), { rubric });
    expect(unknownLens.ok).toBe(false);
    const olderVersion = validateReview(review("i1", "speed", "PASS", { rubricVersion: "v0" }), {
      rubric,
    });
    expect(olderVersion.ok).toBe(true);
  });

  it("rejects malformed verdicts, timestamps and seconds", () => {
    const result = validateReview({
      ...review("i1", "tone", "PASS"),
      verdict: "MAYBE",
      createdAt: "yesterday",
      seconds: -1,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toHaveLength(3);
  });
});

describe("parseItem", () => {
  it("requires an id and an output", () => {
    expect(parseItem({ id: "a", output: null }).ok).toBe(true);
    expect(parseItem({ id: "", meta: 3 })).toEqual({
      ok: false,
      error: ["id must be a non-empty string", "output is required", "meta must be an object"],
    });
  });
});

describe("normalizeSeconds", () => {
  it("rounds plausible durations and drops the rest", () => {
    expect(normalizeSeconds(41.6)).toBe(42);
    expect(normalizeSeconds("90")).toBe(90);
    expect(normalizeSeconds(0)).toBeUndefined();
    expect(normalizeSeconds(3600)).toBeUndefined();
    expect(normalizeSeconds("abc")).toBeUndefined();
  });
});

describe("latestPerLens", () => {
  it("takes the newest verdict per lens and ignores lenses outside the rubric", () => {
    const rows = [
      review("i1", "accuracy", "FAIL"),
      review("i1", "accuracy", "PASS"),
      review("i1", "speed", "FAIL"),
      review("i1", "tone", "PASS"),
    ];
    const current = latestPerLens(rows, rubric.lenses);
    expect(current.map((row) => [row.lens, row.verdict])).toEqual([
      ["accuracy", "PASS"],
      ["tone", "PASS"],
    ]);
  });

  it("honours createdAt over input order, and append order on equal timestamps", () => {
    const later = review("i1", "accuracy", "PASS", { createdAt: "2026-10-02T00:00:00.000Z" });
    const earlier = review("i1", "accuracy", "FAIL", { createdAt: "2026-10-01T00:00:00.000Z" });
    expect(latestPerLens([later, earlier], ["accuracy"])[0]?.verdict).toBe("PASS");
    const a = review("i1", "accuracy", "PASS", { createdAt: "2026-10-01T00:00:00.000Z" });
    const b = review("i1", "accuracy", "NEEDS_REVIEW", { createdAt: "2026-10-01T00:00:00.000Z" });
    expect(latestPerLens([a, b], ["accuracy"])[0]?.verdict).toBe("NEEDS_REVIEW");
  });

  it("names missing lenses in rubric order", () => {
    expect(missingLenses([review("i1", "tone", "PASS")], rubric.lenses)).toEqual([
      "accuracy",
      "policy",
    ]);
  });
});

describe("completeItems", () => {
  it("keeps only items with every lens, scoped to one rubric version", () => {
    const rows = [
      ...triple("i1", "PASS", "PASS", "PASS"),
      review("i2", "accuracy", "PASS"),
      review("i2", "tone", "PASS"),
      ...triple("i3", "PASS", "FAIL", "PASS", { rubricVersion: "v0" }),
    ];
    expect(completeItems(rows, rubric.lenses).map((item) => item.itemId)).toEqual(["i1", "i3"]);
    expect(
      completeItems(rows, rubric.lenses, { rubricVersion: "v1" }).map((item) => item.itemId),
    ).toEqual(["i1"]);
    expect(incompleteItems(rows, rubric.lenses)).toEqual([
      { itemId: "i2", present: ["accuracy", "tone"], missing: ["policy"] },
    ]);
  });
});
