/**
 * The `evalroom` command line. `main()` is a pure-ish function of argv and an IO object so it
 * can be tested in-process; `cli/index.ts` wires it to the real process.
 *
 * @module
 */

import { access, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import { type AgreementReport, agreementReport } from "../agreement";
import { JsonlAuditSink } from "../audit";
import {
  compareRuns,
  type EvalRun,
  type GateResult,
  gate,
  type RunComparison,
  scoreRun,
  summarize,
} from "../evalrun";
import { type GoldenCase, promoteAndRecord, promoteToGolden } from "../golden";
import { isRecord } from "../result";
import { type Item, parseItem, type Review, validateReview } from "../review";
import { parseRubric, type Rubric, VERDICTS } from "../rubric";
import { type PathRule, parseRules } from "../rules";
import { formatInterval, percent } from "../stats";
import {
  appendJsonl,
  JsonlParseError,
  readJsonlLines,
  readRunFile,
  writeRunFile,
} from "../store/jsonl";
import { type Colors, colorEnabled, createColors, table } from "./format";

/** Library and CLI version. */
export const CLI_VERSION = "0.1.0";

/** Exit codes. `gate` uses 0/1/2 for pass/fail/undecidable. */
export const EXIT = {
  /** Success, or gate passed. */
  ok: 0,
  /** Gate failed, promotion refused, or a comparison found broken cases. */
  fail: 1,
  /** Gate undecidable. */
  undecidable: 2,
  /** Bad command line. */
  usage: 64,
  /** Input file missing or malformed. */
  data: 65,
} as const;

/** Where the CLI reads its environment and writes its output. */
export interface CliIO {
  /** Writes to standard output. */
  stdout(text: string): void;
  /** Writes to standard error. */
  stderr(text: string): void;
  /** Environment variables (for NO_COLOR / FORCE_COLOR, USER). */
  env: Readonly<Record<string, string | undefined>>;
  /** Whether stdout is a terminal. */
  isTTY: boolean;
  /** Working directory relative paths resolve against. */
  cwd: string;
}

class UsageError extends Error {}
class DataError extends Error {}

const USAGE = `evalroom ${CLI_VERSION}: human review statistics for LLM outputs

Usage:
  evalroom agreement <reviews.jsonl> --rubric <rubric.json> [--version <v>] [--min-items <n>]
  evalroom gate <run.jsonl> [--min-n 50] [--max-width 0.25] [--min-pass 0.9]
  evalroom compare <base.jsonl> <candidate.jsonl>
  evalroom golden promote <item-id> --items <items.jsonl> --reviews <reviews.jsonl>
                          --rubric <rubric.json> --out <golden.jsonl>
                          [--rules <rules.json>] [--by <name>] [--id <case-id>]
                          [--title <text>] [--allow-split] [--audit <audit.jsonl>]
  evalroom score <golden.jsonl> <outputs.jsonl> --out <run.jsonl> [--id <id>] [--label <label>]

Every command accepts --json for machine-readable output and --help.

Exit codes:
  0  success / gate pass        1  gate fail, promotion refused, or broken cases in compare
  2  gate undecidable           64 usage error              65 bad or missing input file
`;

const COMMON = {
  json: { type: "boolean" },
  help: { type: "boolean", short: "h" },
} as const;

/**
 * Runs the CLI with `argv` (without the node and script path) and returns the exit code.
 */
export async function main(argv: readonly string[], io: CliIO): Promise<number> {
  const c = createColors(colorEnabled(io.isTTY, io.env));
  const [command, ...rest] = argv;
  try {
    switch (command) {
      case "agreement":
        return await agreementCommand(rest, io, c);
      case "gate":
        return await gateCommand(rest, io, c);
      case "compare":
        return await compareCommand(rest, io, c);
      case "golden":
        return await goldenCommand(rest, io, c);
      case "score":
        return await scoreCommand(rest, io, c);
      case "--version":
      case "-v":
      case "version":
        io.stdout(`${CLI_VERSION}\n`);
        return EXIT.ok;
      case undefined:
      case "help":
      case "--help":
      case "-h":
        io.stdout(USAGE);
        return command === undefined ? EXIT.usage : EXIT.ok;
      default:
        throw new UsageError(`unknown command "${command}"`);
    }
  } catch (error) {
    if (error instanceof UsageError) {
      io.stderr(`${c.red("error:")} ${error.message}\n\n${USAGE}`);
      return EXIT.usage;
    }
    if (error instanceof DataError || error instanceof JsonlParseError) {
      io.stderr(`${c.red("error:")} ${error.message}\n`);
      return EXIT.data;
    }
    if (isRecord(error) && typeof error.code === "string" && error.code.startsWith("E")) {
      io.stderr(`${c.red("error:")} ${String((error as { message?: unknown }).message)}\n`);
      return EXIT.data;
    }
    throw error;
  }
}

type Options = Parameters<typeof parseArgs>[0] extends infer T
  ? T extends { options?: infer O }
    ? O
    : never
  : never;

function parse<O extends Options>(args: readonly string[], options: O) {
  try {
    return parseArgs({
      args: [...args],
      options: { ...COMMON, ...options },
      allowPositionals: true,
      strict: true,
    });
  } catch (error) {
    throw new UsageError(error instanceof Error ? error.message : String(error));
  }
}

function need(value: string | undefined, what: string): string {
  if (value === undefined || value === "") throw new UsageError(`missing ${what}`);
  return value;
}

function num(value: string | undefined, flag: string, fallback: number): number {
  if (value === undefined) return fallback;
  const n = Number(value);
  if (!Number.isFinite(n)) throw new UsageError(`${flag} must be a number, got "${value}"`);
  return n;
}

async function readText(io: CliIO, path: string): Promise<string> {
  try {
    return await readFile(resolve(io.cwd, path), "utf8");
  } catch (error) {
    throw new DataError(
      `cannot read ${path}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

async function loadRubric(io: CliIO, path: string): Promise<Rubric> {
  let raw: unknown;
  try {
    raw = JSON.parse(await readText(io, path));
  } catch (error) {
    if (error instanceof DataError) throw error;
    throw new DataError(`${path}: ${error instanceof Error ? error.message : String(error)}`);
  }
  const parsed = parseRubric(raw);
  if (!parsed.ok) throw new DataError(`${path}: invalid rubric: ${parsed.error.join("; ")}`);
  return parsed.value;
}

async function lines(io: CliIO, path: string) {
  try {
    return await readJsonlLines(resolve(io.cwd, path));
  } catch (error) {
    if (error instanceof JsonlParseError) throw error;
    throw new DataError(
      `cannot read ${path}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

async function loadReviews(io: CliIO, path: string, rubric: Rubric): Promise<Review[]> {
  const reviews: Review[] = [];
  for (const { line, value } of await lines(io, path)) {
    const parsed = validateReview(value, { rubric });
    if (!parsed.ok) throw new DataError(`${path}:${line}: ${parsed.error.join("; ")}`);
    reviews.push(parsed.value);
  }
  return reviews;
}

async function loadItems(io: CliIO, path: string): Promise<Item[]> {
  const items: Item[] = [];
  for (const { line, value } of await lines(io, path)) {
    const parsed = parseItem(value);
    if (!parsed.ok) throw new DataError(`${path}:${line}: ${parsed.error.join("; ")}`);
    items.push(parsed.value);
  }
  return items;
}

async function loadGolden(io: CliIO, path: string, missingOk = false): Promise<GoldenCase[]> {
  if (missingOk) {
    try {
      await access(resolve(io.cwd, path));
    } catch {
      return [];
    }
  }
  const cases: GoldenCase[] = [];
  for (const { line, value } of await lines(io, path)) {
    if (!isRecord(value) || typeof value.id !== "string" || typeof value.itemId !== "string") {
      throw new DataError(`${path}:${line}: a golden case needs string id and itemId`);
    }
    const rules = parseRules(value.expectedRules);
    if (!rules.ok) throw new DataError(`${path}:${line}: ${rules.error.join("; ")}`);
    cases.push({ ...(value as unknown as GoldenCase), expectedRules: rules.value });
  }
  return cases;
}

async function loadRun(io: CliIO, path: string): Promise<EvalRun> {
  try {
    return await readRunFile(resolve(io.cwd, path));
  } catch (error) {
    if (error instanceof JsonlParseError) throw error;
    throw new DataError(
      `cannot read ${path}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

function json(io: CliIO, value: unknown): void {
  io.stdout(`${JSON.stringify(value, null, 2)}\n`);
}

// ─── agreement ──────────────────────────────────────────────────────────────

async function agreementCommand(args: readonly string[], io: CliIO, c: Colors): Promise<number> {
  const { values, positionals } = parse(args, {
    rubric: { type: "string" },
    version: { type: "string" },
    "min-items": { type: "string" },
  });
  if (values.help) {
    io.stdout(USAGE);
    return EXIT.ok;
  }
  const reviewsPath = need(positionals[0], "<reviews.jsonl>");
  const rubric = await loadRubric(io, need(values.rubric, "--rubric <rubric.json>"));
  const reviews = await loadReviews(io, reviewsPath, rubric);
  const report = agreementReport(reviews, rubric, {
    ...(values.version !== undefined ? { rubricVersion: values.version } : {}),
    minItems: num(values["min-items"], "--min-items", 10),
  });
  if (values.json) json(io, report);
  else io.stdout(renderAgreement(report, c));
  return EXIT.ok;
}

function kappaText(
  value: number | null,
  band: string | null,
  reason: string | undefined,
  c: Colors,
): string {
  if (value === null) return c.dim(`n/a (${reason ?? "not computed"})`);
  return `${value.toFixed(2)}  ${band ?? ""}`;
}

/** Renders an agreement report for the terminal. */
export function renderAgreement(report: AgreementReport, c: Colors): string {
  const out: string[] = [];
  out.push(
    `${c.bold("evalroom agreement")} · rubric ${c.cyan(report.rubricVersion)} · lenses ${report.lenses.join(", ")}`,
  );
  out.push(
    c.dim(
      `${report.items} complete items · ${report.incompleteItems} incomplete · ${report.excludedReviews} reviews from other rubric versions excluded`,
    ),
  );
  out.push("");
  out.push(
    table([
      [
        "Fleiss' kappa",
        kappaText(report.fleissKappa.value, report.fleissKappa.band, report.fleissKappa.reason, c),
      ],
      ["Raw agreement", formatInterval(report.rawAgreement)],
      [
        "Median review",
        report.medianSeconds === null
          ? c.dim("not recorded")
          : `${Math.round(report.medianSeconds)}s  (${report.lenses
              .map((lens) => {
                const s = report.medianSecondsByLens[lens];
                return `${lens} ${s === null || s === undefined ? "-" : `${Math.round(s)}s`}`;
              })
              .join(" · ")})`,
      ],
    ]),
  );
  out.push("");
  if (report.pairs.length > 0) {
    out.push(
      table(
        report.pairs.map((pair) => [
          `${pair.a} × ${pair.b}`,
          formatInterval(pair.agreement),
          kappaText(pair.cohenKappa.value, pair.cohenKappa.band, undefined, c),
        ]),
        { header: ["Lens pair", "Agreement", "Cohen's kappa"] },
      ),
    );
    out.push("");
  }
  out.push(
    table(
      VERDICTS.map((verdict) => [verdict, formatInterval(report.prevalence[verdict])]),
      { header: ["Verdict", "Prevalence (of all lens verdicts)"] },
    ),
  );
  out.push("");
  if (report.disagreements.length > 0) {
    out.push(c.bold(`Disagreements (${report.disagreements.length})`));
    out.push(
      table(
        report.disagreements.map((d) => [
          d.itemId,
          ...d.verdicts.map((v) => {
            const label = v.errorClass ? `${v.verdict} ${v.errorClass}` : v.verdict;
            return v.verdict === "PASS"
              ? c.green(label)
              : v.verdict === "FAIL"
                ? c.red(label)
                : c.yellow(label);
          }),
        ]),
        { header: ["Item", ...report.lenses] },
      ),
    );
    out.push("");
  }
  out.push(report.interpretation);
  return `${out.join("\n")}\n`;
}

// ─── gate ───────────────────────────────────────────────────────────────────

async function gateCommand(args: readonly string[], io: CliIO, c: Colors): Promise<number> {
  const { values, positionals } = parse(args, {
    "min-n": { type: "string" },
    "max-width": { type: "string" },
    "min-pass": { type: "string" },
  });
  if (values.help) {
    io.stdout(USAGE);
    return EXIT.ok;
  }
  const run = await loadRun(io, need(positionals[0], "<run.jsonl>"));
  const minPassRate = num(values["min-pass"], "--min-pass", 0.9);
  if (minPassRate < 0 || minPassRate > 1)
    throw new UsageError("--min-pass must be between 0 and 1");
  const result = gate(run, {
    minPassRate,
    minN: num(values["min-n"], "--min-n", 50),
    maxWidth: num(values["max-width"], "--max-width", 0.25),
  });
  if (values.json)
    json(io, {
      run: { id: run.id, label: run.label, rubricVersion: run.rubricVersion },
      ...result,
    });
  else io.stdout(renderGate(run, result, c));
  return result.decision === "pass"
    ? EXIT.ok
    : result.decision === "fail"
      ? EXIT.fail
      : EXIT.undecidable;
}

/** Renders a gate result for the terminal. */
export function renderGate(run: EvalRun, result: GateResult, c: Colors): string {
  const label =
    result.decision === "pass"
      ? c.green("PASS")
      : result.decision === "fail"
        ? c.red("FAIL")
        : c.yellow("UNDECIDABLE");
  return `${[
    `${c.bold("evalroom gate")} · run ${c.cyan(run.label)} (${run.id}) · rubric ${run.rubricVersion}`,
    "",
    table([
      ["Decision", label],
      ["Pass rate", formatInterval(result.interval)],
      ["Bar", `${percent(result.minPassRate)} on the lower bound`],
      ["Reason", result.reason],
    ]),
  ].join("\n")}\n`;
}

// ─── compare ────────────────────────────────────────────────────────────────

async function compareCommand(args: readonly string[], io: CliIO, c: Colors): Promise<number> {
  const { values, positionals } = parse(args, {});
  if (values.help) {
    io.stdout(USAGE);
    return EXIT.ok;
  }
  const base = await loadRun(io, need(positionals[0], "<base.jsonl>"));
  const candidate = await loadRun(io, need(positionals[1], "<candidate.jsonl>"));
  const comparison = compareRuns(base, candidate);
  if (values.json) {
    json(io, {
      base: { id: base.id, label: base.label, summary: comparison.baseSummary },
      candidate: { id: candidate.id, label: candidate.label, summary: comparison.candidateSummary },
      broken: comparison.broken.map((row) => ({
        caseId: row.caseId,
        failures: row.candidate.failures,
      })),
      fixed: comparison.fixed.map((row) => ({ caseId: row.caseId, failures: row.base.failures })),
      same: comparison.same.map((row) => ({ caseId: row.caseId, passed: row.base.passed })),
      onlyInBase: comparison.onlyInBase,
      onlyInCandidate: comparison.onlyInCandidate,
      rubricVersionMismatch: comparison.rubricVersionMismatch,
    });
  } else {
    io.stdout(renderComparison(base, candidate, comparison, c));
  }
  return comparison.broken.length > 0 ? EXIT.fail : EXIT.ok;
}

/** Renders a run comparison for the terminal. */
export function renderComparison(
  base: EvalRun,
  candidate: EvalRun,
  comparison: RunComparison,
  c: Colors,
): string {
  const stillFailing = comparison.same.filter((row) => !row.base.passed);
  const out = [
    `${c.bold("evalroom compare")} · ${c.cyan(base.label)} → ${c.cyan(candidate.label)}`,
    "",
    table([
      [base.label, formatInterval(comparison.baseSummary.passRate)],
      [candidate.label, formatInterval(comparison.candidateSummary.passRate)],
    ]),
    "",
    table(
      [
        [
          c.red("broken"),
          String(comparison.broken.length),
          comparison.broken.map((r) => r.caseId).join(", "),
        ],
        [
          c.green("fixed"),
          String(comparison.fixed.length),
          comparison.fixed.map((r) => r.caseId).join(", "),
        ],
        [
          "still failing",
          String(stillFailing.length),
          stillFailing.map((r) => r.caseId).join(", "),
        ],
        ["still passing", String(comparison.same.length - stillFailing.length), ""],
      ],
      { align: ["left", "right", "left"] },
    ),
  ];
  if (comparison.broken.length > 0) {
    out.push("", c.bold("Broken cases"));
    out.push(
      table(
        comparison.broken.flatMap((row) =>
          row.candidate.failures.map((failure) => [row.caseId, failure.code, failure.detail]),
        ),
      ),
    );
  }
  if (comparison.onlyInBase.length + comparison.onlyInCandidate.length > 0) {
    out.push(
      "",
      c.yellow(
        `Not compared: ${comparison.onlyInBase.length} only in ${base.label}, ${comparison.onlyInCandidate.length} only in ${candidate.label}.`,
      ),
    );
  }
  if (comparison.rubricVersionMismatch) {
    out.push(
      c.yellow(
        `Warning: rubric versions differ (${base.rubricVersion} vs ${candidate.rubricVersion}).`,
      ),
    );
  }
  return `${out.join("\n")}\n`;
}

// ─── golden promote ─────────────────────────────────────────────────────────

async function goldenCommand(args: readonly string[], io: CliIO, c: Colors): Promise<number> {
  const [sub, ...rest] = args;
  if (sub !== "promote")
    throw new UsageError(`unknown golden subcommand "${sub ?? ""}" (expected "promote")`);
  const { values, positionals } = parse(rest, {
    items: { type: "string" },
    reviews: { type: "string" },
    rubric: { type: "string" },
    out: { type: "string" },
    rules: { type: "string" },
    by: { type: "string" },
    id: { type: "string" },
    title: { type: "string" },
    "allow-split": { type: "boolean" },
    audit: { type: "string" },
  });
  if (values.help) {
    io.stdout(USAGE);
    return EXIT.ok;
  }
  const itemId = need(positionals[0], "<item-id>");
  const out = need(values.out, "--out <golden.jsonl>");
  const rubric = await loadRubric(io, need(values.rubric, "--rubric <rubric.json>"));
  const items = await loadItems(io, need(values.items, "--items <items.jsonl>"));
  const reviews = await loadReviews(io, need(values.reviews, "--reviews <reviews.jsonl>"), rubric);
  const item = items.find((candidate) => candidate.id === itemId);
  if (!item) throw new DataError(`item "${itemId}" is not in ${values.items}`);

  let rules: PathRule[] = [];
  if (values.rules !== undefined) {
    let raw: unknown;
    try {
      raw = JSON.parse(await readText(io, values.rules));
    } catch (error) {
      if (error instanceof DataError) throw error;
      throw new DataError(
        `${values.rules}: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    const parsed = parseRules(raw);
    if (!parsed.ok) throw new DataError(`${values.rules}: ${parsed.error.join("; ")}`);
    rules = parsed.value;
  } else if (Array.isArray(item.meta?.expectedRules)) {
    const parsed = parseRules(item.meta.expectedRules);
    if (!parsed.ok)
      throw new DataError(`item ${itemId} meta.expectedRules: ${parsed.error.join("; ")}`);
    rules = parsed.value;
  }

  const options = {
    rubric,
    expectedRules: rules,
    promotedBy: values.by ?? io.env.USER ?? io.env.USERNAME ?? "cli",
    requireUnanimousPass: !values["allow-split"],
    ...(values.id !== undefined ? { id: values.id } : {}),
    ...(values.title !== undefined ? { title: values.title } : {}),
  };
  const preview = promoteToGolden(item, reviews, options);
  if (preview.ok) {
    const existing = await loadGolden(io, out, true);
    if (existing.some((golden) => golden.id === preview.value.id)) {
      throw new DataError(`golden case "${preview.value.id}" already exists in ${out}`);
    }
  }
  const result =
    values.audit !== undefined
      ? await promoteAndRecord(
          item,
          reviews,
          options,
          new JsonlAuditSink(resolve(io.cwd, values.audit)),
        )
      : preview;
  if (result.ok) await appendJsonl(resolve(io.cwd, out), [result.value]);

  if (values.json) {
    json(
      io,
      result.ok ? { ok: true, golden: result.value } : { ok: false, refusals: result.error },
    );
  } else if (result.ok) {
    io.stdout(
      `${c.green("promoted")} ${result.value.id} (item ${itemId}, ${result.value.expectedRules.length} rules, reviews ${result.value.provenance.reviewIds.join(", ")}) → ${out}\n`,
    );
  } else {
    io.stdout(`${c.red("refused")} item ${itemId}:\n`);
    for (const refusal of result.error)
      io.stdout(`  ${c.bold(refusal.code)}  ${refusal.message}\n`);
  }
  return result.ok ? EXIT.ok : EXIT.fail;
}

// ─── score ──────────────────────────────────────────────────────────────────

async function scoreCommand(args: readonly string[], io: CliIO, c: Colors): Promise<number> {
  const { values, positionals } = parse(args, {
    out: { type: "string" },
    id: { type: "string" },
    label: { type: "string" },
  });
  if (values.help) {
    io.stdout(USAGE);
    return EXIT.ok;
  }
  const goldenPath = need(positionals[0], "<golden.jsonl>");
  const outputsPath = need(positionals[1], "<outputs.jsonl>");
  const out = need(values.out, "--out <run.jsonl>");
  const cases = await loadGolden(io, goldenPath);
  const outputs = new Map<string, unknown>();
  for (const { line, value } of await lines(io, outputsPath)) {
    if (!isRecord(value) || typeof value.caseId !== "string" || !("output" in value)) {
      throw new DataError(`${outputsPath}:${line}: each line needs a string caseId and an output`);
    }
    if (outputs.has(value.caseId))
      throw new DataError(`${outputsPath}:${line}: duplicate caseId "${value.caseId}"`);
    outputs.set(value.caseId, value.output);
  }
  const id = values.id ?? `run-${new Date().toISOString().replace(/[:.]/g, "-")}`;
  const run = scoreRun(cases, outputs, { id, label: values.label ?? id });
  await writeRunFile(resolve(io.cwd, out), run);
  const summary = summarize(run);
  const unknown = [...outputs.keys()].filter(
    (caseId) => !cases.some((golden) => golden.id === caseId),
  );
  if (values.json) {
    json(io, { id: run.id, label: run.label, out, summary, ignoredOutputs: unknown });
  } else {
    io.stdout(
      `${c.bold("evalroom score")} · ${run.label} · ${formatInterval(summary.passRate)} → ${out}\n`,
    );
    const codes = Object.entries(summary.failuresByCode).sort((a, b) => b[1] - a[1]);
    if (codes.length > 0)
      io.stdout(`${table(codes.map(([code, count]) => [code, String(count)]))}\n`);
    if (unknown.length > 0)
      io.stdout(
        c.yellow(`Ignored ${unknown.length} outputs with no golden case: ${unknown.join(", ")}\n`),
      );
  }
  return EXIT.ok;
}
