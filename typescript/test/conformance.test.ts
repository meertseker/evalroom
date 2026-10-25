import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  CONFORMANCE,
  computeAgreement,
  computeCli,
  computeGolden,
  computeLifecycle,
  plain,
  resolveRef,
  resultsOf,
  sameJson,
} from "../scripts/conformance";
import {
  cohenKappa,
  compareRuns,
  containsPhrase,
  diffValues,
  fleissKappa,
  formatRate,
  type GoldenCase,
  gate,
  getPath,
  interpretKappa,
  median,
  normalizeText,
  type PathRule,
  parseRules,
  scoreOutput,
  scoreRun,
  snapshot,
  summarize,
  wilson,
} from "../src/index";

type Case = Record<string, unknown> & { expected?: unknown };

async function vectors(name: string): Promise<Record<string, Case[]>> {
  return JSON.parse(await readFile(join(CONFORMANCE, name), "utf8")) as Record<string, Case[]>;
}

function check(actual: unknown, c: Case, ignore: string[] = []): void {
  const tolerance = typeof c.tolerance === "number" ? c.tolerance : 1e-9;
  const expected = c.expected;
  let target: unknown = plain(actual);
  if (
    c.published &&
    expected &&
    typeof expected === "object" &&
    target &&
    typeof target === "object"
  ) {
    const subset = target as Record<string, unknown>;
    target = Object.fromEntries(Object.keys(expected).map((key) => [key, subset[key]]));
  }
  expect(sameJson(target, expected, tolerance, "", ignore)).toBeNull();
}

describe("conformance: stats.json", async () => {
  const v = await vectors("stats.json");
  it("wilson", () => {
    for (const c of v.wilson ?? []) {
      check(wilson(c.k as number, c.n as number, c.options as object), c);
    }
  });
  it("formatRate", () => {
    for (const c of v.formatRate ?? []) {
      expect(formatRate(c.k as number, c.n as number, c.options as object)).toBe(c.expected);
    }
  });
  it("fleissKappa, cohenKappa, interpretKappa, median", () => {
    for (const c of v.fleissKappa ?? []) check(fleissKappa(c.rows as never), c);
    for (const c of v.cohenKappa ?? []) check(cohenKappa(c.a as string[], c.b as string[]), c);
    for (const c of v.interpretKappa ?? []) {
      expect(interpretKappa(c.kappa as number | null)).toBe(c.expected);
    }
    for (const c of v.median ?? []) check(median(c.values as number[]), c);
  });
});

describe("conformance: agreement.json and golden.json", async () => {
  const agreement = await vectors("agreement.json");
  const golden = await vectors("golden.json");
  it("agreement reports", () => {
    for (const c of agreement.cases ?? []) check(computeAgreement(c as never), c);
  });
  it("golden promotions and refusals", () => {
    for (const c of golden.cases ?? []) check(computeGolden(c), c);
  });
});

describe("conformance: rules.json", async () => {
  const v = await vectors("rules.json");
  it("scoreOutput, parseRules, normalizeText, containsPhrase, getPath", () => {
    for (const c of v.scoreOutput ?? []) {
      check(scoreOutput(c.output, c.rules as PathRule[]), c);
    }
    for (const c of v.parseRules ?? []) {
      const parsed = parseRules(c.input);
      check(parsed.ok ? { ok: true, value: parsed.value } : { ok: false, issues: parsed.error }, c);
    }
    for (const c of v.normalizeText ?? [])
      expect(normalizeText(c.input as string)).toBe(c.expected);
    for (const c of v.containsPhrase ?? []) {
      expect(containsPhrase(c.haystack as string, c.needle as string)).toBe(c.expected);
    }
    for (const c of v.getPath ?? []) {
      const found = getPath(c.value, c.path as string);
      check({ found: found.found, value: found.value ?? null }, c);
    }
  });
});

describe("conformance: evalrun.json and calibration.json", async () => {
  const v = await vectors("evalrun.json");
  const cal = await vectors("calibration.json");
  it("scoreRun, summarize, compareRuns, gate", () => {
    for (const c of v.scoreRun ?? []) {
      check(
        scoreRun(
          resolveRef(c.cases) as GoldenCase[],
          resolveRef(c.outputs, "outputs") as Record<string, unknown>,
          c.options as never,
        ),
        c,
      );
    }
    for (const c of v.summarize ?? []) check(summarize(resultsOf(c)), c);
    for (const c of v.compareRuns ?? [])
      check(compareRuns(c.base as never, c.candidate as never), c);
    for (const c of v.gate ?? []) {
      if (c.error) expect(() => gate(resultsOf(c), c.options as never)).toThrow(RangeError);
      else check(gate(resultsOf(c), c.options as never), c);
    }
  });
  it("snapshot, diffValues, lifecycle", async () => {
    for (const c of cal.snapshot ?? []) check(snapshot(c.input), c);
    for (const c of cal.diffValues ?? []) check(diffValues(c.before, c.after), c);
    for (const c of cal.lifecycle ?? []) check(await computeLifecycle(c.steps as never), c);
  });
});

describe("conformance: cli/cases.json", async () => {
  const v = await vectors(join("cli", "cases.json"));
  it("every command prints the expected --json and exits with the expected code", async () => {
    for (const c of v.cases ?? []) {
      const result = await computeCli(c.args as string[]);
      expect(result.exit, String(c.name)).toBe(c.exit);
      expect(sameJson(result.stdout, c.stdout, 1e-9, "", (c.ignore as string[]) ?? [])).toBeNull();
    }
  });
});
