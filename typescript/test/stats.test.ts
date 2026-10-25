import { describe, expect, it } from "vitest";
import {
  cohenKappa,
  fleissKappa,
  formatInterval,
  formatRate,
  interpretKappa,
  median,
  wilson,
} from "../src/stats";

describe("wilson", () => {
  // Newcombe (1998), "Two-sided confidence intervals for the single proportion",
  // Statistics in Medicine 17:857-872, Table I, method 3 (score interval without continuity correction).
  it.each([
    [81, 263, 0.2553, 0.3662],
    [15, 148, 0.0624, 0.1605],
    [0, 20, 0, 0.1611],
    [1, 29, 0.0061, 0.1718],
  ])("matches Newcombe (1998) for %i/%i", (k, n, low, high) => {
    const interval = wilson(k, n);
    expect(interval.low).toBeCloseTo(low, 4);
    expect(interval.high).toBeCloseTo(high, 4);
    expect(interval.k).toBe(k);
    expect(interval.n).toBe(n);
  });

  it("matches a hand-computed interval for 12/20", () => {
    // centre = (0.6 + 1.96²/40) / (1 + 1.96²/20) = 0.58389, half-width = 0.19731
    const interval = wilson(12, 20);
    expect(interval.p).toBe(0.6);
    expect(interval.low).toBeCloseTo(0.3866, 4);
    expect(interval.high).toBeCloseTo(0.7812, 4);
  });

  it("is never decidable below minN, however narrow the interval", () => {
    expect(wilson(20, 20).decidable).toBe(false);
    expect(wilson(12, 20).decidable).toBe(false);
  });

  it("is decidable from minN only when the interval is narrow enough", () => {
    expect(wilson(40, 50).decidable).toBe(true); // width 0.218
    expect(wilson(25, 50).decidable).toBe(false); // width 0.267 > 0.25
  });

  it("honours custom minN, maxWidth and z", () => {
    expect(wilson(12, 20, { minN: 10, maxWidth: 0.5 }).decidable).toBe(true);
    expect(wilson(40, 50, { maxWidth: 0.2 }).decidable).toBe(false);
    const wider = wilson(40, 50, { z: 2.576 });
    const narrower = wilson(40, 50);
    expect(wider.high - wider.low).toBeGreaterThan(narrower.high - narrower.low);
  });

  it("returns an empty undecidable interval for n = 0 and non-finite input", () => {
    expect(wilson(0, 0)).toEqual({ p: 0, low: 0, high: 0, k: 0, n: 0, decidable: false });
    expect(wilson(Number.NaN, 10).n).toBe(0);
    expect(wilson(1, Number.POSITIVE_INFINITY).n).toBe(0);
  });

  it("clamps successes into [0, total] and bounds into [0, 1]", () => {
    expect(wilson(70, 50).k).toBe(50);
    expect(wilson(-3, 50).k).toBe(0);
    const all = wilson(50, 50);
    expect(all.high).toBe(1);
    expect(all.low).toBeGreaterThan(0.9);
    expect(wilson(0, 50).low).toBe(0);
  });
});

describe("formatRate", () => {
  it("never prints a bare rate", () => {
    expect(formatRate(12, 20)).toBe("60% (12/20) · 39%–78% · not decidable");
  });

  it("drops the warning once decidable", () => {
    expect(formatRate(80, 100)).toBe("80% (80/100) · 71%–87%");
  });

  it("says there is no data at zero", () => {
    expect(formatRate(0, 0)).toBe("no data · not decidable");
    expect(formatInterval(wilson(0, 0))).toBe("no data · not decidable");
  });
});

describe("median", () => {
  it("handles odd, even, empty and non-finite inputs without mutating", () => {
    const input = [3, 1, 2];
    expect(median(input)).toBe(2);
    expect(input).toEqual([3, 1, 2]);
    expect(median([4, 1, 2, 3])).toBe(2.5);
    expect(median([])).toBeNull();
    expect(median([Number.NaN, 5])).toBe(5);
  });
});

describe("fleissKappa", () => {
  it("reproduces the Wikipedia worked example (10 subjects, 14 raters, 5 categories): 0.210", () => {
    // https://en.wikipedia.org/wiki/Fleiss%27_kappa#Worked_example — P̄ = 0.378, P_e = 0.213.
    const table = [
      [0, 0, 0, 0, 14],
      [0, 2, 6, 4, 2],
      [0, 0, 3, 5, 6],
      [0, 3, 9, 2, 0],
      [2, 2, 8, 1, 1],
      [7, 7, 0, 0, 0],
      [3, 2, 6, 3, 0],
      [2, 5, 3, 2, 2],
      [6, 5, 2, 1, 0],
      [0, 2, 2, 3, 7],
    ];
    const rows = table.map((row) =>
      Object.fromEntries(row.map((count, j) => [`c${j + 1}`, count])),
    );
    expect(fleissKappa(rows)).toBeCloseTo(0.20993, 5);
  });

  it("matches a hand-computed two-category table: P̄ = 0.6, P_e = 0.5 → κ = 0.2", () => {
    const rows = [
      { PASS: 3 },
      { FAIL: 3 },
      { PASS: 2, FAIL: 1 },
      { PASS: 1, FAIL: 2 },
      { PASS: 2, FAIL: 1 },
      { PASS: 1, FAIL: 2 },
      { PASS: 3 },
      { FAIL: 3 },
      { PASS: 2, FAIL: 1 },
      { PASS: 1, FAIL: 2 },
    ];
    expect(fleissKappa(rows)).toBeCloseTo(0.2, 10);
  });

  it("is 1 under perfect agreement with variation between subjects", () => {
    expect(fleissKappa([{ PASS: 3 }, { PASS: 3 }, { FAIL: 3 }])).toBeCloseTo(1, 10);
  });

  it("is negative when raters systematically split", () => {
    expect(
      fleissKappa([
        { PASS: 1, FAIL: 1 },
        { PASS: 1, FAIL: 1 },
      ]),
    ).toBeCloseTo(-1, 10);
  });

  it("is null when undefined: no rows, one rater, or a single category everywhere", () => {
    expect(fleissKappa([])).toBeNull();
    expect(fleissKappa([{ PASS: 1 }])).toBeNull();
    expect(fleissKappa([{ PASS: 3 }, { PASS: 3 }])).toBeNull();
  });

  it("ignores rows whose rater count differs from the first row", () => {
    const base = [{ PASS: 3 }, { FAIL: 3 }];
    expect(fleissKappa([...base, { PASS: 1 }])).toBeCloseTo(fleissKappa(base) as number, 10);
  });
});

describe("cohenKappa", () => {
  it("reproduces the Wikipedia example: p_o = 0.7, p_e = 0.5 → κ = 0.4", () => {
    // 50 subjects: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no.
    const a = [...rep("yes", 20), ...rep("yes", 5), ...rep("no", 10), ...rep("no", 15)];
    const b = [...rep("yes", 20), ...rep("no", 5), ...rep("yes", 10), ...rep("no", 15)];
    expect(cohenKappa(a, b)).toBeCloseTo(0.4, 10);
  });

  it("is 1 for identical varied labels and null when both raters use one category", () => {
    expect(cohenKappa(["a", "b", "a"], ["a", "b", "a"])).toBeCloseTo(1, 10);
    expect(cohenKappa(["a", "a"], ["a", "a"])).toBeNull();
    expect(cohenKappa([], [])).toBeNull();
  });

  it("throws on mismatched lengths", () => {
    expect(() => cohenKappa(["a"], ["a", "b"])).toThrow(RangeError);
  });
});

describe("interpretKappa", () => {
  it("uses the Landis & Koch (1977) bands at their boundaries", () => {
    expect(interpretKappa(-0.01)).toBe("poor");
    expect(interpretKappa(0)).toBe("slight");
    expect(interpretKappa(0.2)).toBe("slight");
    expect(interpretKappa(0.21)).toBe("fair");
    expect(interpretKappa(0.4)).toBe("fair");
    expect(interpretKappa(0.41)).toBe("moderate");
    expect(interpretKappa(0.6)).toBe("moderate");
    expect(interpretKappa(0.61)).toBe("substantial");
    expect(interpretKappa(0.8)).toBe("substantial");
    expect(interpretKappa(0.81)).toBe("almost perfect");
    expect(interpretKappa(1)).toBe("almost perfect");
  });

  it("returns null for null or non-finite kappa", () => {
    expect(interpretKappa(null)).toBeNull();
    expect(interpretKappa(Number.NaN)).toBeNull();
  });
});

function rep(value: string, times: number): string[] {
  return Array.from({ length: times }, () => value);
}
