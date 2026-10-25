/**
 * Items and reviews. A review is one reviewer's verdict on one item through one lens. Reviews
 * are append-only: the latest review per (item, lens) wins, earlier ones are kept as history.
 *
 * @module
 */

import { err, isRecord, ok, type Result } from "./result";
import {
  findErrorClass,
  isSeverity,
  isVerdict,
  type Rubric,
  type Severity,
  type Verdict,
} from "./rubric";

/** One LLM output under review. */
export interface Item {
  /** Stable id, unique within a dataset. */
  id: string;
  /** The output being judged. Any JSON-serialisable value. */
  output: unknown;
  /** The input that produced the output, if you want it frozen alongside. */
  input?: unknown;
  /** Free-form metadata (model, prompt version, trace id, ...). */
  meta?: Record<string, unknown>;
  /** Optional tenant tag for multi-tenant callers. evalroom never filters on it implicitly. */
  tenant?: string;
}

/** One verdict on one item through one lens. */
export interface Review {
  /** Unique id of this review. */
  id: string;
  /** The {@link Item.id} under review. */
  itemId: string;
  /** Lens this verdict is given through. */
  lens: string;
  /** `PASS`, `FAIL` or `NEEDS_REVIEW`. */
  verdict: Verdict;
  /** Error-class code from the rubric. Required on `FAIL` unless validation is relaxed. */
  errorClass?: string;
  /** Severity of the failure. Required on `FAIL` unless validation is relaxed. */
  severity?: Severity;
  /** Free-text reasoning. Required on `FAIL` unless validation is relaxed. */
  note?: string;
  /** Seconds the reviewer spent; feeds the median review time. */
  seconds?: number;
  /** Who gave the verdict. */
  reviewer: string;
  /** {@link Rubric.version} the reviewer graded against. */
  rubricVersion: string;
  /** ISO-8601 timestamp. Decides which review is the latest for its (item, lens). */
  createdAt: string;
  /** Optional tenant tag. */
  tenant?: string;
}

/** Options for {@link validateReview}. */
export interface ValidateReviewOptions {
  /**
   * When given, and the review was written against this rubric's version, the lens must be
   * declared and the error class must exist and be allowed for that lens.
   */
  rubric?: Rubric;
  /** Require `errorClass`, `severity` and `note` on every `FAIL`. Default `true`. */
  requireFailDetails?: boolean;
}

function str(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

/**
 * Validates untrusted input as an {@link Item}.
 */
export function parseItem(input: unknown): Result<Item, string[]> {
  if (!isRecord(input)) return err(["item must be an object"]);
  const issues: string[] = [];
  if (!str(input.id)) issues.push("id must be a non-empty string");
  if (!("output" in input)) issues.push("output is required");
  if (input.meta !== undefined && !isRecord(input.meta)) issues.push("meta must be an object");
  if (input.tenant !== undefined && typeof input.tenant !== "string") {
    issues.push("tenant must be a string");
  }
  if (issues.length > 0) return err(issues);
  const item: Item = { id: input.id as string, output: input.output };
  if ("input" in input) item.input = input.input;
  if (input.meta !== undefined) item.meta = input.meta as Record<string, unknown>;
  if (typeof input.tenant === "string") item.tenant = input.tenant;
  return ok(item);
}

/**
 * Validates untrusted input as a {@link Review}. Returns every issue at once.
 *
 * A `FAIL` must carry `errorClass`, `severity` and `note` (disable with
 * `requireFailDetails: false`). With a `rubric`, reviews stamped with that rubric's version
 * must use a declared lens and an error class allowed for that lens.
 */
export function validateReview(
  input: unknown,
  options: ValidateReviewOptions = {},
): Result<Review, string[]> {
  if (!isRecord(input)) return err(["review must be an object"]);
  const issues: string[] = [];
  for (const field of ["id", "itemId", "lens", "reviewer", "rubricVersion", "createdAt"] as const) {
    if (!str(input[field])) issues.push(`${field} must be a non-empty string`);
  }
  if (str(input.createdAt) && Number.isNaN(Date.parse(input.createdAt))) {
    issues.push("createdAt must be an ISO-8601 timestamp");
  }
  if (!isVerdict(input.verdict)) issues.push("verdict must be PASS, FAIL or NEEDS_REVIEW");
  if (input.severity !== undefined && !isSeverity(input.severity)) {
    issues.push("severity must be low, medium or high");
  }
  if (input.errorClass !== undefined && !str(input.errorClass)) {
    issues.push("errorClass must be a non-empty string when present");
  }
  if (input.note !== undefined && typeof input.note !== "string") {
    issues.push("note must be a string");
  }
  if (
    input.seconds !== undefined &&
    (typeof input.seconds !== "number" || !Number.isFinite(input.seconds) || input.seconds < 0)
  ) {
    issues.push("seconds must be a non-negative number");
  }
  if (input.tenant !== undefined && typeof input.tenant !== "string") {
    issues.push("tenant must be a string");
  }
  if (input.verdict === "FAIL" && options.requireFailDetails !== false) {
    if (!str(input.errorClass)) issues.push("a FAIL requires errorClass");
    if (!isSeverity(input.severity)) issues.push("a FAIL requires severity");
    if (!str(input.note)) issues.push("a FAIL requires a note");
  }
  const rubric = options.rubric;
  if (rubric && input.rubricVersion === rubric.version && str(input.lens)) {
    const lens = input.lens;
    if (!rubric.lenses.includes(lens)) {
      issues.push(`lens "${lens}" is not declared in rubric ${rubric.version}`);
    }
    if (str(input.errorClass)) {
      const entry = findErrorClass(rubric, input.errorClass);
      if (!entry) {
        issues.push(`errorClass "${input.errorClass}" is not in rubric ${rubric.version}`);
      } else if (!entry.lenses.includes(lens)) {
        issues.push(`errorClass "${input.errorClass}" is not available to lens "${lens}"`);
      }
    }
  }
  if (issues.length > 0) return err(issues);
  const review: Review = {
    id: input.id as string,
    itemId: input.itemId as string,
    lens: input.lens as string,
    verdict: input.verdict as Verdict,
    reviewer: input.reviewer as string,
    rubricVersion: input.rubricVersion as string,
    createdAt: input.createdAt as string,
  };
  if (str(input.errorClass)) review.errorClass = input.errorClass;
  if (isSeverity(input.severity)) review.severity = input.severity;
  if (typeof input.note === "string") review.note = input.note;
  if (typeof input.seconds === "number") review.seconds = input.seconds;
  if (typeof input.tenant === "string") review.tenant = input.tenant;
  return ok(review);
}

/** Bounds for {@link normalizeSeconds}. */
export interface SecondsBounds {
  /** Default 1. */
  min?: number;
  /** Default 1800 (thirty minutes: a tab left open is not review time). */
  max?: number;
}

/**
 * Rounds a review duration and drops implausible values: missing, NaN, below `min` or above
 * `max` become `undefined`. Numeric strings are accepted.
 */
export function normalizeSeconds(value: unknown, bounds: SecondsBounds = {}): number | undefined {
  const min = bounds.min ?? 1;
  const max = bounds.max ?? 1800;
  const n = typeof value === "string" && value.trim() ? Number(value) : value;
  if (typeof n !== "number" || !Number.isFinite(n)) return undefined;
  const rounded = Math.round(n);
  return rounded >= min && rounded <= max ? rounded : undefined;
}

/** Groups reviews by item id, preserving first-appearance order of items and input order within an item. */
export function groupByItem<R extends Pick<Review, "itemId">>(
  reviews: readonly R[],
): Map<string, R[]> {
  const byItem = new Map<string, R[]>();
  for (const review of reviews) {
    const list = byItem.get(review.itemId);
    if (list) list.push(review);
    else byItem.set(review.itemId, [review]);
  }
  return byItem;
}

/**
 * The current review per lens for one item: the review with the latest `createdAt`; on equal
 * timestamps the one that appears later in `reviews` wins (append order). Reviews for lenses
 * not in `lenses` are ignored. Returned in `lenses` order, absent lenses omitted.
 */
export function latestPerLens<R extends Pick<Review, "lens" | "createdAt">>(
  reviews: readonly R[],
  lenses: readonly string[],
): R[] {
  const latest = new Map<string, { review: R; at: number; index: number }>();
  reviews.forEach((review, index) => {
    if (!lenses.includes(review.lens)) return;
    const at = Date.parse(review.createdAt);
    const time = Number.isNaN(at) ? Number.NEGATIVE_INFINITY : at;
    const current = latest.get(review.lens);
    if (!current || time > current.at || (time === current.at && index > current.index)) {
      latest.set(review.lens, { review, at: time, index });
    }
  });
  return lenses.flatMap((lens) => {
    const entry = latest.get(lens);
    return entry ? [entry.review] : [];
  });
}

/** Lenses in `lenses` that have no review for this item, in `lenses` order. */
export function missingLenses(
  reviews: readonly Pick<Review, "lens" | "createdAt">[],
  lenses: readonly string[],
): string[] {
  const present = new Set(latestPerLens(reviews, lenses).map((review) => review.lens));
  return lenses.filter((lens) => !present.has(lens));
}

/** An item where every lens has a current verdict. */
export interface CompleteItem {
  /** The item id. */
  itemId: string;
  /** The current review per lens, in rubric lens order. */
  reviews: Review[];
}

/** An item with at least one review but at least one lens still missing. */
export interface IncompleteItem {
  /** The item id. */
  itemId: string;
  /** Lenses with a current verdict. */
  present: string[];
  /** Lenses without one. */
  missing: string[];
}

/** Options for {@link completeItems} and {@link incompleteItems}. */
export interface CoverageOptions {
  /** Only consider reviews stamped with this rubric version. */
  rubricVersion?: string;
}

function coverage(
  reviews: readonly Review[],
  lenses: readonly string[],
  options: CoverageOptions,
): { complete: CompleteItem[]; incomplete: IncompleteItem[] } {
  const scoped =
    options.rubricVersion === undefined
      ? reviews
      : reviews.filter((review) => review.rubricVersion === options.rubricVersion);
  const complete: CompleteItem[] = [];
  const incomplete: IncompleteItem[] = [];
  for (const [itemId, list] of groupByItem(scoped)) {
    const current = latestPerLens(list, lenses);
    if (current.length === lenses.length) {
      complete.push({ itemId, reviews: current });
    } else {
      const present = current.map((review) => review.lens);
      if (present.length === 0) continue;
      incomplete.push({
        itemId,
        present,
        missing: lenses.filter((lens) => !present.includes(lens)),
      });
    }
  }
  return { complete, incomplete };
}

/**
 * Items whose every lens has a current verdict, with that verdict per lens. Only complete items
 * enter agreement statistics or golden promotion.
 */
export function completeItems(
  reviews: readonly Review[],
  lenses: readonly string[],
  options: CoverageOptions = {},
): CompleteItem[] {
  return coverage(reviews, lenses, options).complete;
}

/** Items that have some lens verdicts but not all. Useful as a review queue. */
export function incompleteItems(
  reviews: readonly Review[],
  lenses: readonly string[],
  options: CoverageOptions = {},
): IncompleteItem[] {
  return coverage(reviews, lenses, options).incomplete;
}
