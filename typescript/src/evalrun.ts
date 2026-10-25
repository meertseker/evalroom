/**
 * Eval runs: score outputs against golden cases, summarise them with intervals, compare two
 * runs case by case, and gate a run on its pass rate's confidence interval.
 *
 * @module
 */

import type { GoldenCase } from "./golden";
import { type Rule, type RuleFailure, scoreOutput } from "./rules";
import { formatInterval, type Interval, percent, type WilsonOptions, wilson } from "./stats";

/** The result of scoring one output against one golden case. */
export interface CaseResult {
  /** {@link GoldenCase.id}. */
  caseId: string;
  /** `true` when no rule failed. */
  passed: boolean;
  /** Every failed rule. */
  failures: RuleFailure[];
  /** The output that was scored. */
  output: unknown;
}

/** A scored run over a golden set. */
export interface EvalRun {
  /** Run id. */
  id: string;
  /** Human label, e.g. `"baseline"`, `"prompt-v7"`. */
  label: string;
  /** Rubric version of the golden cases scored. */
  rubricVersion: string;
  /** ISO-8601 timestamp. */
  createdAt?: string;
  /** One result per case. */
  results: CaseResult[];
  /** Optional tenant tag. */
  tenant?: string;
}

/** Options for {@link scoreRun}. */
export interface ScoreRunOptions {
  /** Run id. */
  id: string;
  /** Run label. */
  label: string;
  /** ISO-8601 timestamp. Default: now. */
  createdAt?: string;
  /** Rubric version to stamp. Default: the cases' version (comma-joined if they differ). */
  rubricVersion?: string;
  /** Extra, possibly custom, rules applied to every case after its expected rules. */
  extraRules?: readonly Rule[] | ((golden: GoldenCase) => readonly Rule[]);
  /** Optional tenant tag. */
  tenant?: string;
}

/** Failure code for a golden case that has no output in the run. */
export const MISSING_OUTPUT = "MISSING_OUTPUT";

/**
 * Scores one output per golden case. `outputs` is keyed by case id. A case with no output
 * fails with code {@link MISSING_OUTPUT} rather than being skipped, so a run cannot look
 * better by dropping cases.
 */
export function scoreRun(
  cases: readonly GoldenCase[],
  outputs: ReadonlyMap<string, unknown> | Readonly<Record<string, unknown>>,
  options: ScoreRunOptions,
): EvalRun {
  const lookup =
    outputs instanceof Map
      ? (id: string) => ({ found: outputs.has(id), value: outputs.get(id) })
      : (id: string) => {
          const record = outputs as Readonly<Record<string, unknown>>;
          return { found: Object.hasOwn(record, id), value: record[id] };
        };
  const results = cases.map((golden): CaseResult => {
    const entry = lookup(golden.id);
    if (!entry.found) {
      return {
        caseId: golden.id,
        passed: false,
        failures: [
          { code: MISSING_OUTPUT, path: "", detail: "no output was produced for this case" },
        ],
        output: null,
      };
    }
    const extra =
      typeof options.extraRules === "function"
        ? options.extraRules(golden)
        : (options.extraRules ?? []);
    const score = scoreOutput(entry.value, [...golden.expectedRules, ...extra]);
    return {
      caseId: golden.id,
      passed: score.passed,
      failures: score.failures,
      output: entry.value,
    };
  });
  const versions = [...new Set(cases.map((golden) => golden.rubricVersion))].sort();
  const run: EvalRun = {
    id: options.id,
    label: options.label,
    rubricVersion: options.rubricVersion ?? versions.join(","),
    createdAt: options.createdAt ?? new Date().toISOString(),
    results,
  };
  if (options.tenant !== undefined) run.tenant = options.tenant;
  return run;
}

function resultsOf(run: EvalRun | readonly CaseResult[]): readonly CaseResult[] {
  return Array.isArray(run) ? run : (run as EvalRun).results;
}

/** Output of {@link summarize}. */
export interface RunSummary {
  /** Cases scored. */
  total: number;
  /** Cases passed. */
  passed: number;
  /** Cases failed. */
  failed: number;
  /** Pass rate with its Wilson interval. */
  passRate: Interval;
  /** How many failures each code produced (one case can fail several codes). */
  failuresByCode: Record<string, number>;
}

/** Pass rate (as an interval) and failure counts for a run. */
export function summarize(
  run: EvalRun | readonly CaseResult[],
  options: WilsonOptions = {},
): RunSummary {
  const results = resultsOf(run);
  const passed = results.filter((result) => result.passed).length;
  const failuresByCode: Record<string, number> = {};
  for (const result of results) {
    for (const failure of result.failures) {
      failuresByCode[failure.code] = (failuresByCode[failure.code] ?? 0) + 1;
    }
  }
  return {
    total: results.length,
    passed,
    failed: results.length - passed,
    passRate: wilson(passed, results.length, options),
    failuresByCode,
  };
}

/** One case present in both runs. */
export interface ComparedCase {
  /** The case id. */
  caseId: string;
  /** Its result in the base run. */
  base: CaseResult;
  /** Its result in the candidate run. */
  candidate: CaseResult;
}

/** Output of {@link compareRuns}. */
export interface RunComparison {
  /** Passed in base, failed in candidate: regressions. */
  broken: ComparedCase[];
  /** Failed in base, passed in candidate. */
  fixed: ComparedCase[];
  /** Same outcome in both (both passed or both failed). */
  same: ComparedCase[];
  /** Case ids only the base run scored. */
  onlyInBase: string[];
  /** Case ids only the candidate run scored. */
  onlyInCandidate: string[];
  /** Summary of the base run. */
  baseSummary: RunSummary;
  /** Summary of the candidate run. */
  candidateSummary: RunSummary;
  /** `true` when both runs are {@link EvalRun}s stamped with different rubric versions. */
  rubricVersionMismatch: boolean;
}

/**
 * Partitions the cases both runs scored into broken, fixed and same, in base-run order.
 * Cases present in only one run are listed separately and never counted as fixed or broken.
 */
export function compareRuns(
  base: EvalRun | readonly CaseResult[],
  candidate: EvalRun | readonly CaseResult[],
  options: WilsonOptions = {},
): RunComparison {
  const baseResults = resultsOf(base);
  const candidateResults = resultsOf(candidate);
  const candidateById = new Map(candidateResults.map((result) => [result.caseId, result]));
  const baseIds = new Set(baseResults.map((result) => result.caseId));
  const broken: ComparedCase[] = [];
  const fixed: ComparedCase[] = [];
  const same: ComparedCase[] = [];
  const onlyInBase: string[] = [];
  for (const result of baseResults) {
    const next = candidateById.get(result.caseId);
    if (!next) {
      onlyInBase.push(result.caseId);
      continue;
    }
    const row = { caseId: result.caseId, base: result, candidate: next };
    if (result.passed && !next.passed) broken.push(row);
    else if (!result.passed && next.passed) fixed.push(row);
    else same.push(row);
  }
  const versionOf = (run: EvalRun | readonly CaseResult[]) =>
    Array.isArray(run) ? undefined : (run as EvalRun).rubricVersion;
  const baseVersion = versionOf(base);
  const candidateVersion = versionOf(candidate);
  return {
    broken,
    fixed,
    same,
    onlyInBase,
    onlyInCandidate: candidateResults
      .filter((result) => !baseIds.has(result.caseId))
      .map((result) => result.caseId),
    baseSummary: summarize(baseResults, options),
    candidateSummary: summarize(candidateResults, options),
    rubricVersionMismatch:
      baseVersion !== undefined &&
      candidateVersion !== undefined &&
      baseVersion !== candidateVersion,
  };
}

/** Options for {@link gate}. `minN` defaults to 50 and `maxWidth` to 0.25. */
export interface GateOptions extends WilsonOptions {
  /** The bar the pass rate must clear, in [0, 1]. */
  minPassRate: number;
}

/** `pass`, `fail`, or `undecidable` when the data cannot support either. */
export type GateDecision = "pass" | "fail" | "undecidable";

/** Output of {@link gate}. */
export interface GateResult {
  /** The decision. */
  decision: GateDecision;
  /** The pass rate's Wilson interval the decision rests on. */
  interval: Interval;
  /** The bar that was applied. */
  minPassRate: number;
  /** One sentence explaining the decision. */
  reason: string;
}

/**
 * Gates a run on the Wilson interval of its pass rate.
 *
 * - `undecidable` when `n < minN` or the interval is wider than `maxWidth`: no decision is
 *   taken on too little data, in either direction;
 * - `pass` when the interval's lower bound is at or above `minPassRate`;
 * - `fail` when the point estimate is below `minPassRate`;
 * - `undecidable` otherwise: the point estimate clears the bar but the lower bound does not.
 *
 * @throws {RangeError} when `minPassRate` is not in [0, 1].
 */
export function gate(results: EvalRun | readonly CaseResult[], options: GateOptions): GateResult {
  const { minPassRate } = options;
  if (!Number.isFinite(minPassRate) || minPassRate < 0 || minPassRate > 1) {
    throw new RangeError(`gate: minPassRate must be in [0, 1], got ${String(minPassRate)}`);
  }
  const list = resultsOf(results);
  const passed = list.filter((result) => result.passed).length;
  const interval = wilson(passed, list.length, options);
  const minN = options.minN ?? 50;
  const maxWidth = options.maxWidth ?? 0.25;
  const bar = percent(minPassRate);
  const shown = formatInterval(interval);
  const verdict = (decision: GateDecision, reason: string): GateResult => ({
    decision,
    interval,
    minPassRate,
    reason,
  });
  if (interval.n === 0)
    return verdict("undecidable", "no results; a gate never decides on zero cases");
  if (interval.n < minN) {
    return verdict("undecidable", `n=${interval.n} is below minN=${minN} (${shown})`);
  }
  const width = interval.high - interval.low;
  if (width > maxWidth) {
    return verdict(
      "undecidable",
      `interval width ${width.toFixed(3)} exceeds maxWidth ${maxWidth} (${shown})`,
    );
  }
  if (interval.low >= minPassRate) {
    return verdict("pass", `lower bound ${percent(interval.low)} clears the ${bar} bar (${shown})`);
  }
  if (interval.p < minPassRate) {
    return verdict("fail", `pass rate ${percent(interval.p)} is below the ${bar} bar (${shown})`);
  }
  return verdict(
    "undecidable",
    `pass rate ${percent(interval.p)} meets the ${bar} bar but the lower bound ${percent(interval.low)} does not; collect more cases (${shown})`,
  );
}
