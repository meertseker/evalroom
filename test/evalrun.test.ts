import { describe, expect, it } from "vitest";
import {
  type CaseResult,
  compareRuns,
  type EvalRun,
  gate,
  scoreRun,
  summarize,
} from "../src/evalrun";
import type { GoldenCase } from "../src/golden";
import { custom } from "../src/rules";

function results(passed: number, total: number): CaseResult[] {
  return Array.from({ length: total }, (_, i) => ({
    caseId: `c${i}`,
    passed: i < passed,
    failures: i < passed ? [] : [{ code: "X", path: "", detail: "x" }],
    output: null,
  }));
}

function run(id: string, outcomes: Record<string, boolean>, rubricVersion = "v1"): EvalRun {
  return {
    id,
    label: id,
    rubricVersion,
    results: Object.entries(outcomes).map(([caseId, passed]) => ({
      caseId,
      passed,
      failures: passed ? [] : [{ code: "F", path: "", detail: "f" }],
      output: { caseId },
    })),
  };
}

const golden = (id: string): GoldenCase => ({
  id,
  itemId: id,
  input: null,
  referenceOutput: null,
  expectedRules: [{ path: "reply", op: "notContains", value: "guarantee", code: "PROMISE" }],
  provenance: { reviewIds: [], promotedBy: "m", promotedAt: "2026-10-20T00:00:00.000Z" },
  rubricVersion: "v1",
});

describe("scoreRun", () => {
  it("scores each case, fails missing outputs instead of skipping them, and applies extra rules", () => {
    const evalRun = scoreRun(
      [golden("a"), golden("b"), golden("c")],
      new Map<string, unknown>([
        ["a", { reply: "Your refund is on its way." }],
        ["b", { reply: "We guarantee a refund!" }],
      ]),
      {
        id: "r1",
        label: "baseline",
        createdAt: "2026-10-21T00:00:00.000Z",
        extraRules: [custom("SHORT", (o) => JSON.stringify(o).length < 200)],
      },
    );
    expect(evalRun.rubricVersion).toBe("v1");
    expect(evalRun.results.map((r) => [r.caseId, r.passed, r.failures.map((f) => f.code)])).toEqual(
      [
        ["a", true, []],
        ["b", false, ["PROMISE"]],
        ["c", false, ["MISSING_OUTPUT"]],
      ],
    );
    const fromRecord = scoreRun([golden("a")], { a: { reply: "ok" } }, { id: "r2", label: "x" });
    expect(fromRecord.results[0]?.passed).toBe(true);
  });
});

describe("summarize", () => {
  it("reports the pass rate as an interval and counts failures by code", () => {
    const summary = summarize(results(12, 20));
    expect(summary).toMatchObject({ total: 20, passed: 12, failed: 8, failuresByCode: { X: 8 } });
    expect(summary.passRate).toMatchObject({ k: 12, n: 20, decidable: false });
    expect(summary.passRate.low).toBeCloseTo(0.3866, 4);
  });
});

describe("compareRuns", () => {
  it("partitions shared cases into broken, fixed and same, in base order", () => {
    const comparison = compareRuns(
      run("base", { a: true, b: false, c: true, d: false, e: true }),
      run("candidate", { e: true, d: false, c: false, b: true, a: true, z: true }),
    );
    expect(comparison.broken.map((row) => row.caseId)).toEqual(["c"]);
    expect(comparison.fixed.map((row) => row.caseId)).toEqual(["b"]);
    expect(comparison.same.map((row) => row.caseId)).toEqual(["a", "d", "e"]);
    expect(comparison.onlyInCandidate).toEqual(["z"]);
    expect(comparison.onlyInBase).toEqual([]);
    expect(comparison.broken[0]?.candidate.failures[0]?.code).toBe("F");
    expect(comparison.baseSummary.passRate).toMatchObject({ k: 3, n: 5 });
    expect(comparison.rubricVersionMismatch).toBe(false);
  });

  it("never counts a case missing from one run as fixed or broken, and flags rubric mismatch", () => {
    const comparison = compareRuns(
      run("base", { a: false, b: true }, "v1"),
      run("cand", { a: true }, "v2"),
    );
    expect(comparison.fixed.map((row) => row.caseId)).toEqual(["a"]);
    expect(comparison.broken).toEqual([]);
    expect(comparison.onlyInBase).toEqual(["b"]);
    expect(comparison.rubricVersionMismatch).toBe(true);
  });
});

describe("gate", () => {
  it("is undecidable on zero results", () => {
    expect(gate([], { minPassRate: 0.9 })).toMatchObject({ decision: "undecidable" });
  });

  it("is undecidable below minN even when every case passes", () => {
    const result = gate(results(11, 11), { minPassRate: 0.5 });
    expect(result.decision).toBe("undecidable");
    expect(result.reason).toMatch(/^n=11 is below minN=50/);
    expect(result.interval).toMatchObject({ k: 11, n: 11 });
  });

  it("is undecidable when the interval is wider than maxWidth", () => {
    // 25/50: Wilson 0.366–0.634, width 0.267 > 0.25.
    const result = gate(results(25, 50), { minPassRate: 0.4 });
    expect(result.decision).toBe("undecidable");
    expect(result.reason).toContain("exceeds maxWidth 0.25");
  });

  it("passes only when the lower bound clears the bar", () => {
    // 97/100: Wilson lower bound 0.9154.
    const result = gate(results(97, 100), { minPassRate: 0.9 });
    expect(result.decision).toBe("pass");
    expect(result.interval.low).toBeCloseTo(0.9154, 3);
  });

  it("fails when the point estimate is below the bar", () => {
    expect(gate(results(80, 100), { minPassRate: 0.9 }).decision).toBe("fail");
  });

  it("is undecidable when the point estimate clears the bar but the lower bound does not", () => {
    // 92/100: p = 0.92 >= 0.9, lower bound 0.850 < 0.9.
    const result = gate(results(92, 100), { minPassRate: 0.9 });
    expect(result.decision).toBe("undecidable");
    expect(result.reason).toContain("collect more cases");
  });

  it("honours custom minN and maxWidth, and accepts an EvalRun", () => {
    const evalRun: EvalRun = { id: "r", label: "r", rubricVersion: "v1", results: results(20, 20) };
    expect(gate(evalRun, { minPassRate: 0.8, minN: 20, maxWidth: 0.3 }).decision).toBe("pass");
  });

  it("rejects a minPassRate outside [0, 1]", () => {
    expect(() => gate(results(1, 1), { minPassRate: 90 })).toThrow(RangeError);
  });
});
