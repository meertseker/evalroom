import { describe, expect, it } from "vitest";
import {
  containsPhrase,
  custom,
  deepEqual,
  getPath,
  normalizeText,
  type PathRule,
  parseRules,
  scoreOutput,
} from "../src/rules";

const output = {
  reply: "Hi Sam, your refund of £24.99 is on its way. It can take 5–7 business days.",
  actions: ["refund_requested", "tag:billing"],
  escalate: false,
  confidence: 0.82,
  meta: { lang: "en", flags: ["a", "b"] },
  empty: null,
};

function failCodes(rules: PathRule[]): string[] {
  return scoreOutput(output, rules).failures.map((failure) => failure.code);
}

describe("getPath", () => {
  it("resolves dot paths, numeric indexes, bracket indexes and the root", () => {
    expect(getPath(output, "meta.lang")).toEqual({ found: true, value: "en" });
    expect(getPath(output, "actions.1").value).toBe("tag:billing");
    expect(getPath(output, "meta.flags[1]").value).toBe("b");
    expect(getPath(output, "$.escalate")).toEqual({ found: true, value: false });
    expect(getPath(output, "").value).toBe(output);
    expect(getPath(output, "empty")).toEqual({ found: true, value: null });
  });

  it("reports missing paths without throwing, and ignores inherited properties", () => {
    expect(getPath(output, "meta.nope.deeper").found).toBe(false);
    expect(getPath(output, "actions.9").found).toBe(false);
    expect(getPath(output, "actions.first").found).toBe(false);
    expect(getPath(output, "reply.length").found).toBe(false);
    expect(getPath({}, "toString").found).toBe(false);
  });
});

describe("normalizeText and containsPhrase", () => {
  it("folds case, diacritics, punctuation and whitespace", () => {
    expect(normalizeText("  Full\tREFUND,   guaranteed!  ")).toBe("full refund guaranteed");
    expect(normalizeText("Café Crème")).toBe("cafe creme");
  });

  it("matches whole phrases only", () => {
    expect(containsPhrase("Your REFUND is on its way", "refund")).toBe(true);
    expect(containsPhrase("This item is non-refundable", "refund")).toBe(false);
    expect(containsPhrase("We GUARANTEE   it!", "guarantee it")).toBe(true);
  });
});

describe("scoreOutput", () => {
  it("passes when every rule holds", () => {
    const result = scoreOutput(output, [
      { path: "escalate", op: "eq", value: false },
      { path: "meta", op: "eq", value: { lang: "en", flags: ["a", "b"] } },
      { path: "meta.lang", op: "neq", value: "de" },
      { path: "meta.lang", op: "in", value: ["en", "fr"] },
      { path: "meta.lang", op: "notIn", value: ["de"] },
      { path: "confidence", op: "between", value: [0.5, 0.9] },
      { path: "reply", op: "contains", value: ["refund", "business days"] },
      { path: "reply", op: "notContains", value: ["guarantee", "full card number"] },
      { path: "actions", op: "contains", value: "REFUND_REQUESTED" },
      { path: "reply", op: "matches", value: "^hi ", flags: "i" },
      { path: "reply", op: "notMatches", value: "\\d{16}" },
      { path: "actions", op: "subsetOf", value: ["refund_requested", "tag:billing", "escalate"] },
      { path: "meta.lang", op: "exists" },
      { path: "meta.internal", op: "exists", value: false },
    ]);
    expect(result).toEqual({ passed: true, failures: [] });
  });

  it("reports every failure with code, path and detail, not just the first", () => {
    const result = scoreOutput(output, [
      { path: "escalate", op: "eq", value: true, code: "SHOULD_ESCALATE" },
      { path: "reply", op: "notContains", value: ["on its way", "refund"], code: "FORBIDDEN" },
      { path: "confidence", op: "between", value: [0.9, 1] },
    ]);
    expect(result.passed).toBe(false);
    expect(result.failures).toEqual([
      { code: "SHOULD_ESCALATE", path: "escalate", detail: "expected true, got false" },
      { code: "FORBIDDEN", path: "reply", detail: 'contains forbidden "on its way", "refund"' },
      { code: "between(confidence)", path: "confidence", detail: "0.82 is outside [0.9, 1]" },
    ]);
  });

  it("fails every operator on a missing path except exists", () => {
    const ops = [
      "eq",
      "neq",
      "in",
      "notIn",
      "contains",
      "notContains",
      "matches",
      "subsetOf",
    ] as const;
    for (const op of ops) {
      const rule: PathRule = { path: "missing.field", op, value: ["x"] };
      expect(scoreOutput(output, [rule]).failures[0]?.detail).toBe("missing.field is missing");
    }
    expect(
      scoreOutput(output, [{ path: "missing.field", op: "exists", value: false }]).passed,
    ).toBe(true);
    expect(failCodes([{ path: "missing.field", op: "exists" }])).toEqual(["exists(missing.field)"]);
  });

  it("fails type mismatches instead of coercing", () => {
    expect(failCodes([{ path: "reply", op: "between", value: [0, 1] }])).toHaveLength(1);
    expect(failCodes([{ path: "confidence", op: "contains", value: "8" }])).toHaveLength(1);
    expect(failCodes([{ path: "confidence", op: "matches", value: "0" }])).toHaveLength(1);
    expect(failCodes([{ path: "reply", op: "subsetOf", value: [] }])).toHaveLength(1);
    expect(failCodes([{ path: "confidence", op: "eq", value: "0.82" }])).toHaveLength(1);
  });

  it("flags subsetOf extras and an exists that should be absent", () => {
    const result = scoreOutput(output, [
      { path: "actions", op: "subsetOf", value: ["refund_requested"] },
      { path: "escalate", op: "exists", value: false },
    ]);
    expect(result.failures.map((failure) => failure.detail)).toEqual([
      'not allowed: "tag:billing"',
      "escalate should be absent but is false",
    ]);
  });

  it("runs custom rules and turns thrown errors into failures", () => {
    const result = scoreOutput(output, [
      custom("SHORT_ENOUGH", (o) => (o as typeof output).reply.length < 200),
      custom(
        "HAS_SIGNOFF",
        (o) => (o as typeof output).reply.includes("Thanks") || "no sign-off",
        "reply",
      ),
      custom("BROKEN", () => {
        throw new Error("boom");
      }),
    ]);
    expect(result.failures).toEqual([
      { code: "HAS_SIGNOFF", path: "reply", detail: "no sign-off" },
      { code: "BROKEN", path: "", detail: "custom rule threw: boom" },
    ]);
  });

  it("uses structural equality", () => {
    expect(deepEqual({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] })).toBe(true);
    expect(deepEqual({ a: 1 }, { a: 1, b: undefined })).toBe(false);
    expect(deepEqual([1, 2], [2, 1])).toBe(false);
    expect(deepEqual(Number.NaN, Number.NaN)).toBe(true);
  });
});

describe("parseRules", () => {
  it("accepts well-formed JSON rules", () => {
    const parsed = parseRules([
      { path: "reply", op: "notContains", value: ["guarantee"], code: "PROMISE" },
      { path: "reply", op: "matches", value: "^Hi", flags: "i" },
    ]);
    expect(parsed.ok).toBe(true);
  });

  it("rejects unknown operators, bad operands and invalid regexes, listing all", () => {
    const parsed = parseRules([
      { path: "a", op: "startsWith", value: "x" },
      { path: "a", op: "between", value: [5, 1] },
      { path: "a", op: "in", value: "x" },
      { path: "a", op: "matches", value: "(" },
      { path: "a", op: "notContains", value: ["!!"] },
      { op: "eq" },
    ]);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.error).toHaveLength(6);
  });
});
