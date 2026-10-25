/**
 * Golden cases. A reviewed item becomes a golden case only when every lens has a current
 * verdict and (by default) every one of them is PASS. The case freezes the input, the reviewed
 * output and the rules a future output must satisfy, with provenance back to the reviews.
 *
 * @module
 */

import type { AuditEvent, AuditSink } from "./audit";
import { err, ok, type Result } from "./result";
import { type Item, latestPerLens, missingLenses, type Review } from "./review";
import type { Rubric } from "./rubric";
import { type PathRule, parseRules, scoreOutput } from "./rules";

/** Where a golden case came from. */
export interface GoldenProvenance {
  /** Ids of the current lens reviews the promotion was based on, in rubric lens order. */
  reviewIds: string[];
  /** Who promoted it. */
  promotedBy: string;
  /** ISO-8601 timestamp of the promotion. */
  promotedAt: string;
}

/** A frozen, reviewed case that future outputs are scored against. */
export interface GoldenCase {
  /** Case id. Default `golden-<itemId>`. */
  id: string;
  /** The reviewed {@link Item.id}. */
  itemId: string;
  /** The item's input, frozen. `null` when the item had none. */
  input: unknown;
  /** The output the reviewers graded, frozen. Useful for diffing a candidate against. */
  referenceOutput: unknown;
  /** Rules a new output for this input must satisfy. JSON-serialisable. */
  expectedRules: PathRule[];
  /** Provenance back to the reviews. */
  provenance: GoldenProvenance;
  /** Rubric version the reviews were written against. */
  rubricVersion: string;
  /** Optional human title. */
  title?: string;
  /** Optional tenant tag, copied from the item. */
  tenant?: string;
}

/** Why a promotion was refused. */
export type PromotionRefusalCode =
  | "MISSING_LENSES"
  | "NOT_UNANIMOUS_PASS"
  | "NO_RULES"
  | "INVALID_RULES"
  | "NO_PROMOTER"
  | "REFERENCE_OUTPUT_FAILS";

/** One reason a promotion was refused. */
export interface PromotionRefusal {
  /** Machine code. */
  code: PromotionRefusalCode;
  /** Human-readable explanation. */
  message: string;
  /** Lenses involved, for `MISSING_LENSES` and `NOT_UNANIMOUS_PASS`. */
  lenses?: string[];
}

/** Options for {@link promoteToGolden}. */
export interface PromoteOptions {
  /** The rubric whose lenses must all be present. Only reviews of `rubric.version` count. */
  rubric: Rubric;
  /** Rules future outputs must satisfy. Must be non-empty and valid. */
  expectedRules: readonly PathRule[];
  /** Who is promoting. Required, non-empty. */
  promotedBy: string;
  /** Case id. Default `golden-<itemId>`. */
  id?: string;
  /** Promotion time (ISO-8601). Default: now. */
  promotedAt?: string;
  /** Optional title. */
  title?: string;
  /** Refuse unless every current lens verdict is PASS. Default `true`. */
  requireUnanimousPass?: boolean;
  /**
   * Refuse when the expected rules fail on the item's own reviewed output: rules that reject
   * an output every reviewer passed are almost always wrong. Default `true`.
   */
  checkReferenceOutput?: boolean;
}

/**
 * Promotes a reviewed item to a golden case, or refuses with every reason at once.
 *
 * Preconditions, all checked:
 * 1. every rubric lens has a current review of `rubric.version` for this item;
 * 2. (default) every current verdict is PASS;
 * 3. `expectedRules` is non-empty and valid;
 * 4. `promotedBy` is non-empty;
 * 5. (default) the rules pass on the item's reviewed output.
 *
 * Pure: writes nothing. Use {@link promotionAuditEvent} or {@link promoteAndRecord} to audit.
 */
export function promoteToGolden(
  item: Item,
  reviews: readonly Review[],
  options: PromoteOptions,
): Result<GoldenCase, PromotionRefusal[]> {
  const { rubric } = options;
  const refusals: PromotionRefusal[] = [];
  const relevant = reviews.filter(
    (review) => review.itemId === item.id && review.rubricVersion === rubric.version,
  );
  const missing = missingLenses(relevant, rubric.lenses);
  if (missing.length > 0) {
    refusals.push({
      code: "MISSING_LENSES",
      message: `no current ${rubric.version} verdict for lens${missing.length > 1 ? "es" : ""}: ${missing.join(", ")}`,
      lenses: missing,
    });
  }
  const current = latestPerLens(relevant, rubric.lenses);
  if (options.requireUnanimousPass !== false) {
    const notPass = current.filter((review) => review.verdict !== "PASS");
    if (notPass.length > 0) {
      refusals.push({
        code: "NOT_UNANIMOUS_PASS",
        message: `not every lens passed: ${notPass.map((review) => `${review.lens}=${review.verdict}`).join(", ")}`,
        lenses: notPass.map((review) => review.lens),
      });
    }
  }
  let rules: PathRule[] = [];
  if (options.expectedRules.length === 0) {
    refusals.push({ code: "NO_RULES", message: "a golden case needs at least one expected rule" });
  } else {
    const parsed = parseRules(options.expectedRules);
    if (parsed.ok) rules = parsed.value;
    else refusals.push({ code: "INVALID_RULES", message: parsed.error.join("; ") });
  }
  if (options.promotedBy.trim() === "") {
    refusals.push({ code: "NO_PROMOTER", message: "promotedBy must name who is promoting" });
  }
  if (rules.length > 0 && options.checkReferenceOutput !== false) {
    const score = scoreOutput(item.output, rules);
    if (!score.passed) {
      refusals.push({
        code: "REFERENCE_OUTPUT_FAILS",
        message: `the expected rules fail on the reviewed output: ${score.failures
          .map((failure) => `${failure.code} (${failure.detail})`)
          .join("; ")}`,
      });
    }
  }
  if (refusals.length > 0) return err(refusals);

  const golden: GoldenCase = {
    id: options.id ?? `golden-${item.id}`,
    itemId: item.id,
    input: structuredClone(item.input ?? null),
    referenceOutput: structuredClone(item.output),
    expectedRules: structuredClone(rules),
    provenance: {
      reviewIds: current.map((review) => review.id),
      promotedBy: options.promotedBy,
      promotedAt: options.promotedAt ?? new Date().toISOString(),
    },
    rubricVersion: rubric.version,
  };
  if (options.title !== undefined) golden.title = options.title;
  if (item.tenant !== undefined) golden.tenant = item.tenant;
  return ok(golden);
}

/** The audit event recording a promotion. */
export function promotionAuditEvent(golden: GoldenCase): AuditEvent {
  const event: AuditEvent = {
    at: golden.provenance.promotedAt,
    actor: golden.provenance.promotedBy,
    action: "golden.promote",
    target: golden.id,
    after: {
      itemId: golden.itemId,
      rubricVersion: golden.rubricVersion,
      reviewIds: golden.provenance.reviewIds,
      rules: golden.expectedRules.length,
    },
  };
  if (golden.tenant !== undefined) event.tenant = golden.tenant;
  return event;
}

/**
 * {@link promoteToGolden}, then on success appends the {@link promotionAuditEvent} to `audit`.
 * Refusals are not audited.
 */
export async function promoteAndRecord(
  item: Item,
  reviews: readonly Review[],
  options: PromoteOptions,
  audit: AuditSink,
): Promise<Result<GoldenCase, PromotionRefusal[]>> {
  const result = promoteToGolden(item, reviews, options);
  if (result.ok) await audit.append(promotionAuditEvent(result.value));
  return result;
}
