/**
 * Newline-delimited JSON persistence (node:fs only). Files are append-only; nothing is ever
 * rewritten in place. Also exports the JSONL helpers and the run-file format the CLI uses.
 *
 * Layout of a {@link JsonlStore} directory:
 *
 * ```
 * items.jsonl        one Item per line
 * reviews.jsonl      one Review per line
 * golden.jsonl       one GoldenCase per line
 * calibration.jsonl  one CalibrationVersion per line; the last line per id is current
 * runs/<id>.jsonl    one run file per EvalRun (see formatRunJsonl)
 * ```
 *
 * @module
 */

import { appendFile, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { basename, dirname, extname, join } from "node:path";
import type { CalibrationVersion } from "../calibration";
import type { CaseResult, EvalRun } from "../evalrun";
import type { GoldenCase } from "../golden";
import { isRecord } from "../result";
import type { Item, Review } from "../review";
import type { RuleFailure } from "../rules";
import {
  type CalibrationStore,
  DuplicateIdError,
  type EvalStore,
  matchesReview,
  matchesTenant,
  type ReviewFilter,
  type TenantFilter,
} from "./types";

/** Thrown when a JSONL line is not valid JSON or not the expected shape. */
export class JsonlParseError extends Error {
  /** File (or label) the line came from. */
  readonly source: string;
  /** 1-based line number. */
  readonly line: number;

  constructor(source: string, line: number, message: string) {
    super(`${source}:${line}: ${message}`);
    this.name = "JsonlParseError";
    this.source = source;
    this.line = line;
  }
}

/** One parsed line with its 1-based line number. */
export interface JsonlLine {
  /** 1-based line number in the source. */
  line: number;
  /** Parsed JSON value. */
  value: unknown;
}

/**
 * Parses JSONL text. Blank lines are skipped, a UTF-8 BOM and CRLF line endings are accepted.
 *
 * @throws {JsonlParseError} with the source and line number on invalid JSON.
 */
export function parseJsonlLines(text: string, source = "<input>"): JsonlLine[] {
  const lines = text.replace(/^﻿/, "").split(/\r?\n/);
  const out: JsonlLine[] = [];
  lines.forEach((raw, index) => {
    if (raw.trim() === "") return;
    try {
      out.push({ line: index + 1, value: JSON.parse(raw) });
    } catch (error) {
      throw new JsonlParseError(
        source,
        index + 1,
        error instanceof Error ? error.message : String(error),
      );
    }
  });
  return out;
}

/** {@link parseJsonlLines}, values only. */
export function parseJsonl(text: string, source = "<input>"): unknown[] {
  return parseJsonlLines(text, source).map((entry) => entry.value);
}

/** Serialises records as JSONL (one compact JSON value per line, trailing newline). */
export function toJsonl(records: readonly unknown[]): string {
  return records.map((record) => `${JSON.stringify(record)}\n`).join("");
}

function isMissing(error: unknown): boolean {
  return (error as NodeJS.ErrnoException | undefined)?.code === "ENOENT";
}

/** Reads a JSONL file. A missing file is an error unless `missingOk` is set, then `[]`. */
export async function readJsonlLines(
  path: string,
  options: { missingOk?: boolean } = {},
): Promise<JsonlLine[]> {
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch (error) {
    if (options.missingOk && isMissing(error)) return [];
    throw error;
  }
  return parseJsonlLines(text, path);
}

/** {@link readJsonlLines}, values only. */
export async function readJsonl(
  path: string,
  options: { missingOk?: boolean } = {},
): Promise<unknown[]> {
  return (await readJsonlLines(path, options)).map((entry) => entry.value);
}

/** Appends records to a JSONL file, creating it and its directory if needed. */
export async function appendJsonl(path: string, records: readonly unknown[]): Promise<void> {
  if (records.length === 0) return;
  await mkdir(dirname(path), { recursive: true });
  await appendFile(path, toJsonl(records), "utf8");
}

/** First line of a run file. */
export interface RunHeader {
  /** Discriminator. */
  kind: "run";
  /** Run id. */
  id: string;
  /** Run label. */
  label: string;
  /** Rubric version. */
  rubricVersion: string;
  /** ISO-8601 timestamp. */
  createdAt?: string;
  /** Optional tenant tag. */
  tenant?: string;
}

/**
 * Serialises a run as a run file: a `{"kind":"run",...}` header line followed by one
 * {@link CaseResult} per line.
 */
export function formatRunJsonl(run: EvalRun): string {
  const header: RunHeader = {
    kind: "run",
    id: run.id,
    label: run.label,
    rubricVersion: run.rubricVersion,
  };
  if (run.createdAt !== undefined) header.createdAt = run.createdAt;
  if (run.tenant !== undefined) header.tenant = run.tenant;
  return toJsonl([header, ...run.results]);
}

function parseFailures(value: unknown, source: string, line: number): RuleFailure[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new JsonlParseError(source, line, "failures must be an array");
  return value.map((failure) => {
    if (!isRecord(failure) || typeof failure.code !== "string") {
      throw new JsonlParseError(source, line, "each failure needs a string code");
    }
    return {
      code: failure.code,
      path: typeof failure.path === "string" ? failure.path : "",
      detail: typeof failure.detail === "string" ? failure.detail : "",
    };
  });
}

/**
 * Parses a run file. The header line is optional: without it, `fallback` (or the source name)
 * supplies id and label and the rubric version is `"unknown"`.
 *
 * @throws {JsonlParseError} on invalid JSON or a malformed result line.
 */
export function parseRunJsonl(
  text: string,
  source = "<input>",
  fallback: { id?: string; label?: string } = {},
): EvalRun {
  const lines = parseJsonlLines(text, source);
  let header: Partial<RunHeader> = {};
  const results: CaseResult[] = [];
  const seen = new Set<string>();
  lines.forEach(({ line, value }, index) => {
    if (!isRecord(value))
      throw new JsonlParseError(source, line, "each line must be a JSON object");
    if (value.kind === "run") {
      if (index !== 0)
        throw new JsonlParseError(source, line, "the run header must be the first line");
      header = value as Partial<RunHeader>;
      return;
    }
    if (typeof value.caseId !== "string" || value.caseId === "") {
      throw new JsonlParseError(source, line, "caseId must be a non-empty string");
    }
    if (typeof value.passed !== "boolean")
      throw new JsonlParseError(source, line, "passed must be a boolean");
    if (seen.has(value.caseId))
      throw new JsonlParseError(source, line, `duplicate caseId "${value.caseId}"`);
    seen.add(value.caseId);
    results.push({
      caseId: value.caseId,
      passed: value.passed,
      failures: parseFailures(value.failures, source, line),
      output: value.output ?? null,
    });
  });
  const name = basename(source, extname(source));
  const run: EvalRun = {
    id: typeof header.id === "string" ? header.id : (fallback.id ?? name),
    label: typeof header.label === "string" ? header.label : (fallback.label ?? name),
    rubricVersion: typeof header.rubricVersion === "string" ? header.rubricVersion : "unknown",
    results,
  };
  if (typeof header.createdAt === "string") run.createdAt = header.createdAt;
  if (typeof header.tenant === "string") run.tenant = header.tenant;
  return run;
}

/** Reads a run file from disk. See {@link parseRunJsonl}. */
export async function readRunFile(path: string): Promise<EvalRun> {
  return parseRunJsonl(await readFile(path, "utf8"), path);
}

/** Writes a run file, replacing any file at `path`. See {@link formatRunJsonl}. */
export async function writeRunFile(path: string, run: EvalRun): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, formatRunJsonl(run), "utf8");
}

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/**
 * A file-backed store: {@link EvalStore} and {@link CalibrationStore} over JSONL files in one
 * directory. Every write appends; duplicates are detected by reading the file first, so run
 * a single writer per directory.
 */
export class JsonlStore<TConfig = unknown> implements EvalStore, CalibrationStore<TConfig> {
  /** Directory holding the files. Created on first write. */
  readonly dir: string;

  /** @param dir directory for the store's files. */
  constructor(dir: string) {
    this.dir = dir;
  }

  #file(name: string): string {
    return join(this.dir, name);
  }

  async #all<V>(name: string): Promise<V[]> {
    return (await readJsonl(this.#file(name), { missingOk: true })) as V[];
  }

  async #insert<V extends { id: string }>(name: string, kind: string, value: V): Promise<void> {
    const existing = await this.#all<V>(name);
    if (existing.some((record) => record.id === value.id))
      throw new DuplicateIdError(kind, value.id);
    await appendJsonl(this.#file(name), [value]);
  }

  /** @inheritdoc */
  async addItem(item: Item): Promise<void> {
    await this.#insert("items.jsonl", "item", item);
  }

  /** @inheritdoc */
  async getItem(id: string): Promise<Item | undefined> {
    return (await this.#all<Item>("items.jsonl")).find((item) => item.id === id);
  }

  /** @inheritdoc */
  async listItems(filter?: TenantFilter): Promise<Item[]> {
    return (await this.#all<Item>("items.jsonl")).filter((item) => matchesTenant(item, filter));
  }

  /** @inheritdoc */
  async addReview(review: Review): Promise<void> {
    await this.#insert("reviews.jsonl", "review", review);
  }

  /** @inheritdoc */
  async listReviews(filter?: ReviewFilter): Promise<Review[]> {
    return (await this.#all<Review>("reviews.jsonl")).filter((review) =>
      matchesReview(review, filter),
    );
  }

  /** @inheritdoc */
  async addGolden(golden: GoldenCase): Promise<void> {
    await this.#insert("golden.jsonl", "golden case", golden);
  }

  /** @inheritdoc */
  async getGolden(id: string): Promise<GoldenCase | undefined> {
    return (await this.#all<GoldenCase>("golden.jsonl")).find((golden) => golden.id === id);
  }

  /** @inheritdoc */
  async listGolden(filter?: TenantFilter): Promise<GoldenCase[]> {
    return (await this.#all<GoldenCase>("golden.jsonl")).filter((golden) =>
      matchesTenant(golden, filter),
    );
  }

  #runPath(id: string): string {
    if (!SAFE_ID.test(id)) {
      throw new RangeError(`run id "${id}" must match ${SAFE_ID} to be used as a file name`);
    }
    return join(this.dir, "runs", `${id}.jsonl`);
  }

  /** @inheritdoc */
  async addRun(run: EvalRun): Promise<void> {
    const path = this.#runPath(run.id);
    await mkdir(dirname(path), { recursive: true });
    try {
      await writeFile(path, formatRunJsonl(run), { encoding: "utf8", flag: "wx" });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "EEXIST")
        throw new DuplicateIdError("run", run.id);
      throw error;
    }
  }

  /** @inheritdoc */
  async getRun(id: string): Promise<EvalRun | undefined> {
    try {
      return await readRunFile(this.#runPath(id));
    } catch (error) {
      if (isMissing(error)) return undefined;
      throw error;
    }
  }

  /** @inheritdoc */
  async listRuns(filter?: TenantFilter): Promise<EvalRun[]> {
    let names: string[];
    try {
      names = await readdir(join(this.dir, "runs"));
    } catch (error) {
      if (isMissing(error)) return [];
      throw error;
    }
    const runs = await Promise.all(
      names
        .filter((name) => name.endsWith(".jsonl"))
        .sort()
        .map((name) => readRunFile(join(this.dir, "runs", name))),
    );
    return runs.filter((run) => matchesTenant(run, filter));
  }

  /** @inheritdoc */
  async listVersions(): Promise<CalibrationVersion<TConfig>[]> {
    const latest = new Map<string, CalibrationVersion<TConfig>>();
    for (const version of await this.#all<CalibrationVersion<TConfig>>("calibration.jsonl")) {
      latest.set(version.id, version);
    }
    return [...latest.values()].sort((a, b) => a.version - b.version);
  }

  /** @inheritdoc */
  async getVersion(id: string): Promise<CalibrationVersion<TConfig> | undefined> {
    return (await this.listVersions()).find((version) => version.id === id);
  }

  /** @inheritdoc */
  async saveVersion(version: CalibrationVersion<TConfig>): Promise<void> {
    await appendJsonl(this.#file("calibration.jsonl"), [version]);
  }
}
