/**
 * Shared conformance machinery: the inputs of every generated vector, and the functions that
 * turn an input into the JSON output the TypeScript implementation produces. The generator
 * writes those outputs to `conformance/*.json`; the TypeScript and Python test suites recompute
 * them and must match (floats within 1e-9, everything else exactly).
 *
 * @module
 */

import { readFileSync } from "node:fs";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { type CliIO, main } from "../src/cli/main";
import {
  activate,
  agreementReport,
  type CalibrationVersion,
  type CaseResult,
  cohenKappa,
  compareRuns,
  containsPhrase,
  createDraft,
  diffValues,
  type EvalRun,
  fleissKappa,
  formatRate,
  type GoldenCase,
  gate,
  getPath,
  type Item,
  interpretKappa,
  MemoryAuditSink,
  MemoryStore,
  median,
  normalizeText,
  type PathRule,
  parseJsonl,
  parseRubric,
  parseRules,
  promoteToGolden,
  type Review,
  type Rubric,
  rollback,
  scoreOutput,
  scoreRun,
  snapshot,
  summarize,
  wilson,
} from "../src/index";

/** Repository root. */
export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
/** `conformance/` at the repository root. */
export const CONFORMANCE = join(ROOT, "conformance");
/** `conformance/data/`. */
export const DATA = join(CONFORMANCE, "data");

/** A JSON value. */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

/** Round-trips a value through JSON: drops undefined, turns class instances into plain data. */
export function plain<T>(value: T): Json {
  return JSON.parse(JSON.stringify(value)) as Json;
}

async function readJson(name: string): Promise<Json> {
  return JSON.parse(await readFile(join(DATA, name), "utf8")) as Json;
}

async function readLines(name: string): Promise<Json[]> {
  return parseJsonl(await readFile(join(DATA, name), "utf8"), name) as Json[];
}

function asRubric(raw: unknown): Rubric {
  const parsed = parseRubric(raw);
  if (!parsed.ok) throw new Error(parsed.error.join("; "));
  return parsed.value;
}

// ─── inputs ─────────────────────────────────────────────────────────────────

const RUBRIC_V1 = {
  version: "v1",
  lenses: ["accuracy", "tone", "policy"],
  errorClasses: [
    {
      code: "WRONG_FACT",
      label: "Wrong fact",
      lenses: ["accuracy"],
      include: "States something false.",
      exclude: "Incomplete but not false.",
    },
    {
      code: "DISMISSIVE",
      label: "Dismissive tone",
      lenses: ["tone"],
      include: "Blames the customer.",
      exclude: "Firm but polite is fine.",
    },
    {
      code: "UNSUPPORTED_PROMISE",
      label: "Unsupported promise",
      lenses: ["policy", "accuracy"],
      include: "Promises what policy does not allow.",
      exclude: "Quoting policy is fine.",
    },
  ],
};

type V = "PASS" | "FAIL" | "NEEDS_REVIEW";

/** Deterministic review factory, as in test/fixtures.ts. */
class Reviews {
  #tick = 0;
  readonly rows: Review[] = [];

  add(itemId: string, lens: string, verdict: V, extra: Partial<Review> = {}): this {
    this.#tick += 1;
    const review: Review = {
      id: `r${this.#tick}`,
      itemId,
      lens,
      verdict,
      reviewer: `${lens}-reviewer`,
      rubricVersion: "v1",
      createdAt: new Date(Date.UTC(2026, 9, 1, 0, 0, this.#tick)).toISOString(),
    };
    if (verdict === "FAIL") {
      review.errorClass =
        lens === "tone" ? "DISMISSIVE" : lens === "policy" ? "UNSUPPORTED_PROMISE" : "WRONG_FACT";
      review.severity = "medium";
      review.note = "fails";
    }
    this.rows.push({ ...review, ...extra });
    return this;
  }

  triple(itemId: string, a: V, t: V, p: V, extra: Partial<Review> = {}): this {
    return this.add(itemId, "accuracy", a, extra)
      .add(itemId, "tone", t, extra)
      .add(itemId, "policy", p, extra);
  }
}

function seeded(
  n: number,
  f: (i: number) => [V, V, V],
  extra: (i: number) => Partial<Review> = () => ({}),
) {
  const r = new Reviews();
  for (let i = 0; i < n; i += 1) {
    const [a, t, p] = f(i);
    r.triple(`i${i}`, a, t, p, extra(i));
  }
  return r.rows;
}

/** A small deterministic PRNG (mulberry32) so generated grids are stable. */
function prng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const VERDICT_LIST: V[] = ["PASS", "FAIL", "NEEDS_REVIEW"];

/** Inputs for `stats.json` (generated part). */
export function statsInputs() {
  const wilsonCases: Array<{ k: number; n: number; options?: Record<string, number> }> = [];
  for (const n of [0, 1, 2, 5, 11, 19, 20, 49, 50, 51, 100, 263, 1000]) {
    const ks = new Set([0, 1, Math.floor(n / 3), Math.floor(n / 2), n - 1, n]);
    for (const k of ks) {
      if (k >= 0 && k <= n) wilsonCases.push({ k, n });
    }
  }
  wilsonCases.push(
    { k: 70, n: 50 },
    { k: -3, n: 50 },
    { k: 12, n: 20, options: { minN: 10, maxWidth: 0.5 } },
    { k: 40, n: 50, options: { maxWidth: 0.2 } },
    { k: 40, n: 50, options: { z: 2.576 } },
    { k: 7.5, n: 10 },
  );
  const formatRateCases = [
    { k: 12, n: 20 },
    { k: 80, n: 100 },
    { k: 0, n: 0 },
    { k: 9, n: 11 },
    { k: 125, n: 1000 },
    { k: 605, n: 1000 },
    { k: 50, n: 50 },
    { k: 12, n: 20, options: { minN: 10, maxWidth: 0.5 } },
  ];
  const random = prng(7);
  const fleissCases: Array<{ rows: Array<Record<string, number>> }> = [
    { rows: [] },
    { rows: [{ PASS: 1 }] },
    { rows: [{ PASS: 3 }, { PASS: 3 }] },
    { rows: [{ PASS: 3 }, { PASS: 3 }, { FAIL: 3 }] },
    {
      rows: [
        { PASS: 1, FAIL: 1 },
        { PASS: 1, FAIL: 1 },
      ],
    },
    { rows: [{ PASS: 3 }, { FAIL: 3 }, { PASS: 1 }] },
  ];
  for (let c = 0; c < 6; c += 1) {
    const raters = 2 + c;
    const rows: Array<Record<string, number>> = [];
    for (let s = 0; s < 8 + c * 3; s += 1) {
      const row: Record<string, number> = {};
      for (let r = 0; r < raters; r += 1) {
        const v = VERDICT_LIST[Math.floor(random() * 3)] as V;
        row[v] = (row[v] ?? 0) + 1;
      }
      rows.push(row);
    }
    fleissCases.push({ rows });
  }
  const cohenCases: Array<{ a: string[]; b: string[] }> = [
    { a: [], b: [] },
    { a: ["a", "a"], b: ["a", "a"] },
    { a: ["a", "b", "a"], b: ["a", "b", "a"] },
    { a: ["a", "b"], b: ["b", "a"] },
  ];
  for (let c = 0; c < 5; c += 1) {
    const a: string[] = [];
    const b: string[] = [];
    for (let i = 0; i < 10 + c * 7; i += 1) {
      const x = VERDICT_LIST[Math.floor(random() * (2 + (c % 2)))] as string;
      a.push(x);
      b.push(random() < 0.7 ? x : (VERDICT_LIST[Math.floor(random() * 3)] as string));
    }
    cohenCases.push({ a, b });
  }
  const kappas = [null, -1, -0.01, 0, 0.2, 0.2000001, 0.21, 0.4, 0.41, 0.6, 0.61, 0.8, 0.81, 1];
  const medians = [[], [3, 1, 2], [4, 1, 2, 3], [5], [1.5, 2.5], [10, -2, 7, 7]];
  return { wilsonCases, formatRateCases, fleissCases, cohenCases, kappas, medians };
}

/** Inputs for `agreement.json`. */
export async function agreementInputs() {
  const mixed = new Reviews();
  for (let i = 0; i < 6; i += 1) mixed.triple(`p${i}`, "PASS", "PASS", "PASS");
  mixed.triple("f1", "FAIL", "FAIL", "FAIL").triple("f2", "FAIL", "FAIL", "FAIL");
  mixed.triple("s1", "PASS", "FAIL", "PASS").triple("s2", "PASS", "PASS", "FAIL");
  const latest = new Reviews()
    .add("i1", "accuracy", "FAIL")
    .triple("i1", "PASS", "PASS", "PASS")
    .add("i2", "accuracy", "PASS")
    .add("i2", "tone", "PASS");
  const timed = new Reviews()
    .triple("i1", "PASS", "PASS", "FAIL", { seconds: 60 })
    .triple("i2", "PASS", "FAIL", "FAIL", { seconds: 180 })
    .triple("i3", "NEEDS_REVIEW", "PASS", "FAIL", { seconds: 41 });
  const random = prng(11);
  const noisy = seeded(
    60,
    () =>
      [0, 1, 2].map(() =>
        random() < 0.75 ? "PASS" : random() < 0.6 ? "FAIL" : "NEEDS_REVIEW",
      ) as [V, V, V],
    (i) => (i % 4 === 0 ? { seconds: 20 + i } : {}),
  );
  const solo = { version: "v1", lenses: ["accuracy"], errorClasses: [] };
  return [
    {
      name: "support-replies, current rubric",
      rubric: "data/rubric.json",
      reviews: "data/reviews.jsonl",
      options: {},
    },
    {
      name: "support-replies, older rubric version",
      rubric: "data/rubric.json",
      reviews: "data/reviews.jsonl",
      options: { rubricVersion: "2026-09.3" },
    },
    {
      name: "support-replies, minItems 25",
      rubric: "data/rubric.json",
      reviews: "data/reviews.jsonl",
      options: { minItems: 25 },
    },
    { name: "hand-computed kappa 0.65909", rubric: RUBRIC_V1, reviews: mixed.rows, options: {} },
    { name: "latest verdict per lens wins", rubric: RUBRIC_V1, reviews: latest.rows, options: {} },
    { name: "median seconds and confusion", rubric: RUBRIC_V1, reviews: timed.rows, options: {} },
    {
      name: "nine items: kappa withheld",
      rubric: RUBRIC_V1,
      reviews: seeded(9, (i) => ["PASS", "PASS", i % 2 ? "FAIL" : "PASS"]),
      options: {},
    },
    {
      name: "twelve unanimous PASS: kappa undefined",
      rubric: RUBRIC_V1,
      reviews: seeded(12, () => ["PASS", "PASS", "PASS"]),
      options: {},
    },
    { name: "sixty noisy items", rubric: RUBRIC_V1, reviews: noisy, options: {} },
    {
      name: "single lens",
      rubric: solo,
      reviews: seeded(12, () => ["PASS", "PASS", "PASS"]).filter((r) => r.lens === "accuracy"),
      options: {},
    },
  ];
}

const OUTPUT = {
  reply: "Hi Sam, your refund of £24.99 is on its way. It can take 5–7 business days.",
  actions: ["refund_requested", "tag:billing"],
  escalate: false,
  confidence: 0.82,
  meta: { lang: "en", flags: ["a", "b"] },
  empty: null,
  count: 3,
  nested: [{ id: 1 }, { id: 2 }],
};

/** Inputs for `rules.json`. */
export function rulesInputs() {
  const r = (path: string, op: string, value?: unknown, more: Record<string, unknown> = {}) => {
    const rule: Record<string, unknown> = { path, op, ...more };
    if (value !== undefined) rule.value = value;
    return rule;
  };
  const scoreOutputCases = [
    {
      name: "every operator passing",
      output: OUTPUT,
      rules: [
        r("escalate", "eq", false),
        r("meta", "eq", { lang: "en", flags: ["a", "b"] }),
        r("meta.lang", "neq", "de"),
        r("meta.lang", "in", ["en", "fr"]),
        r("meta.lang", "notIn", ["de"]),
        r("confidence", "between", [0.5, 0.9]),
        r("reply", "contains", ["refund", "business days"]),
        r("reply", "notContains", ["guarantee", "full card number"]),
        r("actions", "contains", "REFUND_REQUESTED"),
        r("reply", "matches", "^hi ", { flags: "i" }),
        r("reply", "notMatches", "\\d{16}"),
        r("actions", "subsetOf", ["refund_requested", "tag:billing", "escalate"]),
        r("meta.lang", "exists"),
        r("meta.internal", "exists", false),
        r("nested[1].id", "eq", 2),
        r("$.count", "between", [3, 3]),
      ],
    },
    {
      name: "every failure reported",
      output: OUTPUT,
      rules: [
        r("escalate", "eq", true, { code: "SHOULD_ESCALATE" }),
        r("reply", "notContains", ["on its way", "refund"], { code: "FORBIDDEN" }),
        r("confidence", "between", [0.9, 1]),
        r("meta.lang", "neq", "en"),
        r("meta.lang", "in", ["de", "fr"]),
        r("meta.lang", "notIn", ["en"]),
        r("reply", "contains", ["refund", "next day", "£24.99", "5-7"]),
        r("reply", "matches", "^bye/now", { flags: "mi" }),
        r("reply", "notMatches", "refund"),
        r("actions", "subsetOf", ["refund_requested"]),
        r("escalate", "exists", false),
        r("count", "eq", 3.5),
        r("meta", "eq", { lang: "en" }),
        r("confidence", "eq", "0.82"),
        r("escalate", "eq", 0),
        r("empty", "eq", false),
        r("actions", "contains", ["tag billing", "TAG:BILLING", 3]),
      ],
    },
    {
      name: "missing paths",
      output: OUTPUT,
      rules: [
        "eq",
        "neq",
        "in",
        "notIn",
        "between",
        "contains",
        "notContains",
        "matches",
        "notMatches",
        "subsetOf",
      ]
        .map((op) => r("missing.field", op, ["x"]))
        .concat([
          r("missing.field", "exists"),
          r("missing.field", "exists", false),
          r("actions.9", "eq", 1),
        ]),
    },
    {
      name: "type mismatches",
      output: OUTPUT,
      rules: [
        r("reply", "between", [0, 1]),
        r("confidence", "contains", "8"),
        r("confidence", "matches", "0"),
        r("reply", "subsetOf", []),
        r("meta", "contains", "en"),
        r("empty", "between", [0, 1]),
        r("count", "notMatches", "3"),
      ],
    },
    {
      name: "long values truncated, unicode kept",
      output: { a: "x".repeat(100), b: "é".repeat(90), c: ["ü", "ß", "naïve café"] },
      rules: [
        r("a", "eq", "y"),
        r("b", "eq", "z"),
        r("c", "contains", ["Naive Cafe", "ss"]),
        r("c", "notContains", ["ü"]),
      ],
    },
    {
      name: "root path and whole output",
      output: ["only", "strings"],
      rules: [
        r("", "subsetOf", ["only", "strings", "more"]),
        r("$", "eq", ["only"]),
        r("0", "eq", "only"),
      ],
    },
  ];
  const parseRulesCases = [
    {
      name: "well-formed",
      input: [
        r("reply", "notContains", ["guarantee"], { code: "PROMISE" }),
        r("reply", "matches", "^Hi", { flags: "i" }),
        r("a", "exists"),
        r("a", "exists", false),
        r("a", "between", [1, 1]),
      ],
    },
    {
      name: "every problem listed",
      input: [
        r("a", "startsWith", "x"),
        r("a", "between", [5, 1]),
        r("a", "in", "x"),
        r("a", "matches", "("),
        r("a", "notContains", ["!!"]),
        { op: "eq" },
        r("a", "contains"),
        r("a", "contains", []),
        r("a", "exists", "yes"),
        r("a", "eq", 1, { flags: 3, code: "" }),
        "not an object",
        r("a", "matches", 5),
        r("a", "between", [0, "1"]),
        r("a", "subsetOf", { a: 1 }),
      ],
    },
    { name: "not an array", input: { path: "a", op: "eq" } },
  ];
  const texts = [
    "  Full\tREFUND,   guaranteed!  ",
    "Café Crème",
    "£24.99 is on its way",
    "5–7 business days",
    "naïve — résumé",
    "ÅNGSTRÖM ﬁne",
    "!!!",
    "",
  ];
  const phrases = [
    ["Your REFUND is on its way", "refund"],
    ["This item is non-refundable", "refund"],
    ["We GUARANTEE   it!", "guarantee it"],
    ["anything", "!!"],
    ["Card ending 4242", "card number"],
    ["The café is open", "cafe"],
  ];
  const paths = [
    "meta.lang",
    "actions.1",
    "meta.flags[1]",
    "$.escalate",
    "empty",
    "meta.nope.deeper",
    "actions.9",
    "actions.first",
    "reply.length",
    "nested.0.id",
    "$",
    "",
  ];
  return { scoreOutputCases, parseRulesCases, texts, phrases, paths, pathValue: OUTPUT };
}

/** Inputs for `golden.json`. */
export async function goldenInputs() {
  const items = (await readLines("items.jsonl")) as Array<{ id: string } & Record<string, Json>>;
  const expected = (await readJson("expected-rules.json")) as Record<string, Json>;
  const fallback = expected["t-01"];
  const cases: Array<Record<string, unknown>> = items.map((item) => ({
    name: `support-replies ${item.id}`,
    rubric: "data/rubric.json",
    item: `data/items.jsonl#${item.id}`,
    reviews: "data/reviews.jsonl",
    options: {
      expectedRules: expected[item.id] ?? fallback,
      promotedBy: "support-qa-lead",
      promotedAt: "2026-10-12T16:00:00.000Z",
    },
  }));
  const item = {
    id: "t-1",
    input: { message: "Where is my refund?" },
    output: { reply: "Your refund was issued on 3 October and takes 5–7 business days." },
    tenant: "acme",
  };
  const rules = [{ path: "reply", op: "notContains", value: ["guarantee"], code: "PROMISE" }];
  const base = {
    expectedRules: rules,
    promotedBy: "maintainer",
    promotedAt: "2026-10-20T10:00:00.000Z",
  };
  const pass = new Reviews().triple("t-1", "PASS", "PASS", "PASS").rows;
  const split = new Reviews().triple("t-1", "PASS", "NEEDS_REVIEW", "FAIL").rows;
  cases.push(
    {
      name: "unanimous pass with title and id",
      rubric: RUBRIC_V1,
      item,
      reviews: pass,
      options: { ...base, id: "case-9", title: "Refund timing" },
    },
    { name: "split refused", rubric: RUBRIC_V1, item, reviews: split, options: base },
    {
      name: "split allowed",
      rubric: RUBRIC_V1,
      item,
      reviews: split,
      options: { ...base, requireUnanimousPass: false },
    },
    {
      name: "every refusal",
      rubric: RUBRIC_V1,
      item,
      reviews: [],
      options: { ...base, expectedRules: [], promotedBy: " " },
    },
    {
      name: "invalid rules",
      rubric: RUBRIC_V1,
      item,
      reviews: pass,
      options: { ...base, expectedRules: [{ path: "reply", op: "between", value: "x" }] },
    },
    {
      name: "rules reject the reviewed output",
      rubric: RUBRIC_V1,
      item,
      reviews: pass,
      options: {
        ...base,
        expectedRules: [{ path: "reply", op: "contains", value: "30 days", code: "WINDOW" }],
      },
    },
    {
      name: "reference check disabled",
      rubric: RUBRIC_V1,
      item: { id: "t-1", output: { reply: "x" } },
      reviews: pass,
      options: {
        ...base,
        expectedRules: [{ path: "reply", op: "contains", value: "30 days" }],
        checkReferenceOutput: false,
      },
    },
  );
  return cases;
}

/**
 * Resolves a reference to a file in `conformance/`: `"data/x.json"` is the parsed file,
 * `"data/x.jsonl"` its lines, `"data/x.jsonl#id"` the line with that id. With `key` "outputs",
 * an outputs file becomes a caseId -> output record. Any other value is returned unchanged.
 */
export function resolveRef(value: unknown, key?: string): unknown {
  if (typeof value !== "string" || !value.startsWith("data/")) return value;
  const [file, id] = value.split("#") as [string, string | undefined];
  const text = readFileSync(join(CONFORMANCE, file), "utf8");
  if (!file.endsWith(".jsonl")) return JSON.parse(text);
  const rows = parseJsonl(text, file) as Array<Record<string, unknown>>;
  if (id !== undefined) return rows.find((row) => row.id === id);
  if (key === "outputs") return Object.fromEntries(rows.map((row) => [row.caseId, row.output]));
  return rows;
}

/** Builds `total` case results of which the first `passed` pass, from a compact vector. */
export function resultsOf(c: Record<string, unknown>): CaseResult[] {
  return caseResults(Number(c.passed), Number(c.total), typeof c.code === "string" ? c.code : "X");
}

function caseResults(passed: number, total: number, code = "X"): CaseResult[] {
  return Array.from({ length: total }, (_, i) => ({
    caseId: `c${i}`,
    passed: i < passed,
    failures: i < passed ? [] : [{ code, path: "", detail: code.toLowerCase() }],
    output: null,
  }));
}

function outcomes(id: string, map: Record<string, boolean>, rubricVersion = "v1"): EvalRun {
  return {
    id,
    label: id,
    rubricVersion,
    results: Object.entries(map).map(([caseId, passed]) => ({
      caseId,
      passed,
      failures: passed ? [] : [{ code: "F", path: "", detail: "f" }],
      output: { caseId },
    })),
  };
}

/** Inputs for `evalrun.json`. */
export async function evalrunInputs() {
  const outputsCandidate = await readLines("outputs-candidate.jsonl");
  const asRecord = (rows: Json[]) =>
    Object.fromEntries(
      (rows as Array<{ caseId: string; output: Json }>).map((row) => [row.caseId, row.output]),
    );
  const partial = asRecord(outputsCandidate);
  delete partial["golden-t-04"];
  partial.extra = { reply: "ignored" };
  const scoreRunCases = [
    {
      name: "support-replies base",
      cases: "data/golden.jsonl",
      outputs: "data/outputs-base.jsonl",
      options: { id: "base", label: "reply-v3", createdAt: "2026-10-14T09:00:00.000Z" },
    },
    {
      name: "support-replies candidate",
      cases: "data/golden.jsonl",
      outputs: "data/outputs-candidate.jsonl",
      options: { id: "candidate", label: "reply-v4", createdAt: "2026-10-15T09:00:00.000Z" },
    },
    {
      name: "missing output fails",
      cases: "data/golden.jsonl",
      outputs: partial,
      options: {
        id: "p",
        label: "p",
        createdAt: "2026-10-16T00:00:00.000Z",
        rubricVersion: "override",
      },
    },
  ];
  const summarizeCases = [
    { passed: 12, total: 20 },
    { passed: 0, total: 0 },
    { passed: 49, total: 50, code: "Y" },
  ];
  const compareCases = [
    {
      name: "partition in base order",
      base: outcomes("base", { a: true, b: false, c: true, d: false, e: true }),
      candidate: outcomes("candidate", { e: true, d: false, c: false, b: true, a: true, z: true }),
    },
    {
      name: "rubric mismatch and one-sided",
      base: outcomes("base", { a: false, b: true }, "v1"),
      candidate: outcomes("cand", { a: true }, "v2"),
    },
  ];
  const gateCases: Array<{
    name?: string;
    passed: number;
    total: number;
    options: Record<string, number>;
  }> = [];
  for (const [k, n] of [
    [0, 0],
    [11, 11],
    [25, 50],
    [40, 50],
    [45, 50],
    [97, 100],
    [92, 100],
    [80, 100],
    [90, 100],
    [180, 200],
    [181, 200],
    [190, 200],
    [380, 400],
    [20, 20],
  ] as const) {
    for (const bar of [0, 0.5, 0.8, 0.9, 0.95, 1]) {
      gateCases.push({ passed: k, total: n, options: { minPassRate: bar } });
    }
  }
  gateCases.push(
    {
      name: "custom minN and maxWidth",
      passed: 20,
      total: 20,
      options: { minPassRate: 0.8, minN: 20, maxWidth: 0.3 },
    },
    {
      name: "maxWidth as an integer",
      passed: 9,
      total: 11,
      options: { minPassRate: 0.5, minN: 5, maxWidth: 1 },
    },
    { name: "bar out of range", passed: 1, total: 1, options: { minPassRate: 90 } },
  );
  return { scoreRunCases, summarizeCases, compareCases, gateCases };
}

/** Inputs for `calibration.json`. */
export function calibrationInputs() {
  const snapshots = [
    { b: 1, a: { d: [1, 2], c: "x" } },
    [3, { z: null, y: [true, false] }],
    "plain",
    { nested: { deeper: { b: 2, a: 1 } } },
  ];
  const diffs = [
    [
      { model: "m-1", temperature: 0.2, retry: { max: 3 } },
      { model: "m-2", temperature: 0.2, retry: { max: 5 }, stop: ["\n"] },
    ],
    [{ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] }],
    [{ a: [1, 2] }, { a: [2, 1] }],
    [1, 2],
    [{ long: "x".repeat(80) }, { long: "y" }],
    [{ gone: true }, {}],
    [null, { a: 1 }],
  ];
  const lifecycle = [
    {
      name: "draft, four-eyes activation, rollback",
      steps: [
        {
          op: "createDraft",
          input: {
            config: { model: "m-1", temperature: 0.2 },
            reason: "initial",
            author: "ana",
            at: "2026-10-01T00:00:00.000Z",
          },
        },
        {
          op: "activate",
          input: { id: "cal-1", actor: "ben", reason: "approved", at: "2026-10-02T00:00:00.000Z" },
        },
        {
          op: "createDraft",
          input: {
            config: { temperature: 0.2, model: "m-2" },
            reason: "try m-2",
            author: "ana",
            at: "2026-10-03T00:00:00.000Z",
          },
        },
        {
          op: "activate",
          input: { id: "cal-2", actor: "ana", reason: "ship it", at: "2026-10-04T00:00:00.000Z" },
        },
        {
          op: "activate",
          input: {
            id: "cal-2",
            actor: "ben",
            reason: "kappa held",
            at: "2026-10-04T00:00:00.000Z",
          },
        },
        {
          op: "activate",
          input: { id: "cal-1", actor: "ben", reason: "again", at: "2026-10-05T00:00:00.000Z" },
        },
        {
          op: "activate",
          input: { id: "nope", actor: "ben", reason: "x", at: "2026-10-05T00:00:00.000Z" },
        },
        {
          op: "createDraft",
          input: {
            config: { model: "m-3" },
            reason: "  ",
            author: "ana",
            at: "2026-10-06T00:00:00.000Z",
          },
        },
        {
          op: "createDraft",
          input: {
            config: { model: "m-3" },
            reason: "m-3",
            author: "ana",
            id: "custom",
            at: "2026-10-06T00:00:00.000Z",
          },
        },
        {
          op: "activate",
          input: {
            id: "custom",
            actor: "ana",
            reason: "self",
            allowSelfActivation: true,
            at: "2026-10-07T00:00:00.000Z",
          },
        },
        {
          op: "rollback",
          input: { actor: "ben", reason: "m-3 regressed on tone", at: "2026-10-08T00:00:00.000Z" },
        },
        {
          op: "rollback",
          input: { actor: "ben", reason: "again", at: "2026-10-09T00:00:00.000Z" },
        },
        {
          op: "rollback",
          input: { actor: "", reason: "no actor", at: "2026-10-09T00:00:00.000Z" },
        },
      ],
    },
    {
      name: "rollback on an empty store",
      steps: [
        { op: "rollback", input: { actor: "ben", reason: "x", at: "2026-10-01T00:00:00.000Z" } },
      ],
    },
  ];
  return { snapshots, diffs, lifecycle };
}

/** Inputs for `cli/cases.json`. Commands run with the working directory set to a copy of `conformance/data`. */
export function cliInputs() {
  const promote = (id: string, more: string[] = []) => [
    "golden",
    "promote",
    id,
    "--items",
    "items.jsonl",
    "--reviews",
    "reviews.jsonl",
    "--rubric",
    "rubric.json",
    "--out",
    "out/golden.jsonl",
    "--by",
    "qa",
    "--json",
    ...more,
  ];
  return [
    {
      name: "agreement",
      args: ["agreement", "reviews.jsonl", "--rubric", "rubric.json", "--json"],
    },
    {
      name: "agreement older version",
      args: [
        "agreement",
        "reviews.jsonl",
        "--rubric",
        "rubric.json",
        "--version",
        "2026-09.3",
        "--json",
      ],
    },
    {
      name: "agreement min-items",
      args: [
        "agreement",
        "reviews.jsonl",
        "--rubric",
        "rubric.json",
        "--min-items",
        "30",
        "--json",
      ],
    },
    { name: "gate candidate default", args: ["gate", "run-candidate.jsonl", "--json"] },
    {
      name: "gate candidate loose pass",
      args: [
        "gate",
        "run-candidate.jsonl",
        "--min-pass",
        "0.5",
        "--min-n",
        "5",
        "--max-width",
        "1",
        "--json",
      ],
    },
    {
      name: "gate base fail",
      args: [
        "gate",
        "run-base.jsonl",
        "--min-pass",
        "0.9",
        "--min-n",
        "5",
        "--max-width",
        "1",
        "--json",
      ],
    },
    {
      name: "gate straddle",
      args: [
        "gate",
        "run-candidate.jsonl",
        "--min-pass",
        "0.8",
        "--min-n",
        "5",
        "--max-width",
        "1",
        "--json",
      ],
    },
    {
      name: "compare base to candidate",
      args: ["compare", "run-base.jsonl", "run-candidate.jsonl", "--json"],
    },
    {
      name: "compare candidate to base",
      args: ["compare", "run-candidate.jsonl", "run-base.jsonl", "--json"],
    },
    { name: "compare identical", args: ["compare", "run-base.jsonl", "run-base.jsonl", "--json"] },
    {
      name: "score candidate",
      args: [
        "score",
        "golden.jsonl",
        "outputs-candidate.jsonl",
        "--out",
        "out/run.jsonl",
        "--id",
        "c2",
        "--label",
        "again",
        "--json",
      ],
    },
    {
      name: "score base",
      args: [
        "score",
        "golden.jsonl",
        "outputs-base.jsonl",
        "--out",
        "out/base.jsonl",
        "--id",
        "b2",
        "--json",
      ],
    },
    {
      name: "golden promote t-01",
      args: promote("t-01", ["--rules", "rules-t-01.json", "--title", "Refund status"]),
      ignore: ["golden.provenance.promotedAt"],
    },
    { name: "golden promote split item", args: promote("t-03", ["--rules", "rules-t-01.json"]) },
    { name: "golden promote without rules", args: promote("t-02") },
    {
      name: "golden promote allow split",
      args: promote("t-03", ["--rules", "rules-t-01.json", "--allow-split", "--id", "split-3"]),
      ignore: ["golden.provenance.promotedAt"],
    },
    { name: "usage error", args: ["agreement", "reviews.jsonl", "--json"] },
    { name: "unknown command", args: ["frobnicate"] },
    { name: "missing file", args: ["gate", "nope.jsonl", "--json"] },
    { name: "bad bar", args: ["gate", "run-base.jsonl", "--min-pass", "2", "--json"] },
  ];
}

// ─── outputs ────────────────────────────────────────────────────────────────

/** Computes `stats.json` entries (generated part). */
export function computeStats() {
  const inputs = statsInputs();
  return {
    wilson: inputs.wilsonCases.map((c) => ({ ...c, expected: plain(wilson(c.k, c.n, c.options)) })),
    formatRate: inputs.formatRateCases.map((c) => ({
      ...c,
      expected: formatRate(c.k, c.n, c.options),
    })),
    fleissKappa: inputs.fleissCases.map((c) => ({ ...c, expected: fleissKappa(c.rows) })),
    cohenKappa: inputs.cohenCases.map((c) => ({ ...c, expected: cohenKappa(c.a, c.b) })),
    interpretKappa: inputs.kappas.map((kappa) => ({ kappa, expected: interpretKappa(kappa) })),
    median: inputs.medians.map((values) => ({ values, expected: median(values) })),
  };
}

/** Computes one agreement vector's output. */
export function computeAgreement(c: {
  rubric: unknown;
  reviews: unknown;
  options: Record<string, unknown>;
}): Json {
  return plain(
    agreementReport(resolveRef(c.reviews) as Review[], asRubric(resolveRef(c.rubric)), c.options),
  );
}

/** Computes rules vectors. */
export function computeRules() {
  const inputs = rulesInputs();
  return {
    scoreOutput: inputs.scoreOutputCases.map((c) => ({
      ...c,
      expected: plain(scoreOutput(c.output, c.rules as unknown as PathRule[])),
    })),
    parseRules: inputs.parseRulesCases.map((c) => {
      const parsed = parseRules(c.input);
      return {
        ...c,
        expected: parsed.ok
          ? { ok: true, value: plain(parsed.value) }
          : { ok: false, issues: parsed.error },
      };
    }),
    normalizeText: inputs.texts.map((input) => ({ input, expected: normalizeText(input) })),
    containsPhrase: inputs.phrases.map(([haystack, needle]) => ({
      haystack,
      needle,
      expected: containsPhrase(haystack as string, needle as string),
    })),
    getPath: inputs.paths.map((path) => {
      const found = getPath(inputs.pathValue, path);
      return {
        value: inputs.pathValue,
        path,
        expected: { found: found.found, value: plain(found.value ?? null) },
      };
    }),
  };
}

/** Computes one golden vector's output. */
export function computeGolden(c: Record<string, unknown>): Json {
  const options = c.options as Record<string, unknown>;
  const result = promoteToGolden(resolveRef(c.item) as Item, resolveRef(c.reviews) as Review[], {
    ...(options as object),
    rubric: asRubric(resolveRef(c.rubric)),
    expectedRules: options.expectedRules as never,
    promotedBy: options.promotedBy as string,
  });
  return result.ok
    ? { ok: true, value: plain(result.value) }
    : { ok: false, error: plain(result.error) };
}

/** Computes evalrun vectors. */
export async function computeEvalrun() {
  const inputs = await evalrunInputs();
  return {
    scoreRun: inputs.scoreRunCases.map((c) => ({
      ...c,
      expected: plain(
        scoreRun(
          resolveRef(c.cases) as GoldenCase[],
          resolveRef(c.outputs, "outputs") as Record<string, unknown>,
          c.options,
        ),
      ),
    })),
    summarize: inputs.summarizeCases.map((c) => ({
      ...c,
      expected: plain(summarize(resultsOf(c))),
    })),
    compareRuns: inputs.compareCases.map((c) => ({
      ...c,
      expected: plain(compareRuns(c.base, c.candidate)),
    })),
    gate: inputs.gateCases.map((c) => {
      try {
        return { ...c, expected: plain(gate(resultsOf(c), c.options as { minPassRate: number })) };
      } catch {
        return { ...c, error: true };
      }
    }),
  };
}

/** Runs one calibration lifecycle and returns step results, final versions and audit events. */
export async function computeLifecycle(
  steps: Array<{ op: string; input: Record<string, unknown> }>,
): Promise<Json> {
  const store = new MemoryStore<unknown>();
  const audit = new MemoryAuditSink();
  const results: Json[] = [];
  for (const step of steps) {
    let result: { ok: true; value: CalibrationVersion<unknown> } | { ok: false; error: unknown };
    if (step.op === "createDraft") result = await createDraft(store, step.input as never, audit);
    else if (step.op === "activate") result = await activate(store, step.input as never, audit);
    else result = await rollback(store, step.input as never, audit);
    results.push(plain(result));
  }
  return { results, versions: plain(await store.listVersions()), audit: plain(await audit.list()) };
}

/** Computes calibration vectors. */
export async function computeCalibration() {
  const inputs = calibrationInputs();
  const lifecycle = [];
  for (const c of inputs.lifecycle)
    lifecycle.push({ ...c, expected: await computeLifecycle(c.steps) });
  return {
    snapshot: inputs.snapshots.map((input) => ({ input, expected: plain(snapshot(input)) })),
    diffValues: inputs.diffs.map(([before, after]) => ({
      before,
      after,
      expected: diffValues(before, after),
    })),
    lifecycle,
  };
}

/** Runs the CLI in a fresh copy of `conformance/data`; returns exit code and parsed stdout. */
export async function computeCli(args: string[]): Promise<{ exit: number; stdout: Json }> {
  const dir = await mkdtemp(join(tmpdir(), "evalroom-conformance-"));
  try {
    await cp(DATA, dir, { recursive: true });
    let stdout = "";
    const io: CliIO = {
      stdout: (text) => {
        stdout += text;
      },
      stderr: () => {},
      env: { NO_COLOR: "1" },
      isTTY: false,
      cwd: dir,
    };
    const exit = await main(args, io);
    let parsed: Json = null;
    if (stdout.trim().startsWith("{")) parsed = JSON.parse(stdout) as Json;
    return { exit, stdout: parsed };
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

/** Deep comparison with a float tolerance; keys listed in `ignore` (dot paths) are skipped. */
export function sameJson(
  actual: unknown,
  expected: unknown,
  tolerance = 1e-9,
  path = "",
  ignore: string[] = [],
): string | null {
  if (ignore.includes(path)) return null;
  if (typeof expected === "number" && typeof actual === "number") {
    if (Number.isInteger(expected) && Number.isInteger(actual))
      return actual === expected ? null : `${path}: ${actual} !== ${expected}`;
    return Math.abs(actual - expected) <= tolerance ? null : `${path}: ${actual} != ${expected}`;
  }
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length)
      return `${path}: array length differs`;
    for (let i = 0; i < expected.length; i += 1) {
      const problem = sameJson(actual[i], expected[i], tolerance, `${path}[${i}]`, ignore);
      if (problem) return problem;
    }
    return null;
  }
  if (expected !== null && typeof expected === "object") {
    if (actual === null || typeof actual !== "object" || Array.isArray(actual))
      return `${path}: not an object`;
    const a = actual as Record<string, unknown>;
    const e = expected as Record<string, unknown>;
    const keys = new Set([...Object.keys(a), ...Object.keys(e)]);
    for (const key of keys) {
      const child = path ? `${path}.${key}` : key;
      if (ignore.includes(child)) continue;
      if (!(key in a) || !(key in e)) return `${child}: present on one side only`;
      const problem = sameJson(a[key], e[key], tolerance, child, ignore);
      if (problem) return problem;
    }
    return null;
  }
  return Object.is(actual, expected)
    ? null
    : `${path}: ${JSON.stringify(actual)} !== ${JSON.stringify(expected)}`;
}
