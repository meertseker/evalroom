/** Loaders shared by the example scripts. */

import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  type Item,
  type PathRule,
  parseItem,
  parseJsonl,
  parseRubric,
  type Review,
  type Rubric,
  validateReview,
} from "../../src/index";

/**
 * Directory holding this example's data. The data is shared with the Python package and the
 * conformance tests, so it lives in `conformance/data` at the repository root.
 */
export const here = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "conformance",
  "data",
);

/** Path of a data file of this example. */
export function file(name: string): string {
  return join(here, name);
}

/** Reads and validates rubric.json. */
export async function loadRubric(): Promise<Rubric> {
  const parsed = parseRubric(JSON.parse(await readFile(file("rubric.json"), "utf8")));
  if (!parsed.ok) throw new Error(parsed.error.join("; "));
  return parsed.value;
}

/** Reads and validates items.jsonl. */
export async function loadItems(): Promise<Item[]> {
  return parseJsonl(await readFile(file("items.jsonl"), "utf8"), "items.jsonl").map((raw) => {
    const parsed = parseItem(raw);
    if (!parsed.ok) throw new Error(parsed.error.join("; "));
    return parsed.value;
  });
}

/** Reads and validates reviews.jsonl against the rubric. */
export async function loadReviews(rubric: Rubric): Promise<Review[]> {
  return parseJsonl(await readFile(file("reviews.jsonl"), "utf8"), "reviews.jsonl").map((raw) => {
    const parsed = validateReview(raw, { rubric });
    if (!parsed.ok) throw new Error(parsed.error.join("; "));
    return parsed.value;
  });
}

/** Reads expected-rules.json: item id → rules for its golden case. */
export async function loadExpectedRules(): Promise<Record<string, PathRule[]>> {
  return JSON.parse(await readFile(file("expected-rules.json"), "utf8")) as Record<
    string,
    PathRule[]
  >;
}

/** Reads an outputs file: one `{ caseId, output }` per line. */
export async function loadOutputs(name: string): Promise<Map<string, unknown>> {
  const rows = parseJsonl(await readFile(file(name), "utf8"), name) as Array<{
    caseId: string;
    output: unknown;
  }>;
  return new Map(rows.map((row) => [row.caseId, row.output]));
}
