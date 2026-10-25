/**
 * Inter-lens agreement. Answers "can we trust our graders?" for one rubric version: raw
 * agreement, Fleiss' kappa across lenses, Cohen's kappa per lens pair, prevalence, pairwise
 * confusion, review time and the list of disagreements.
 *
 * @module
 */

import { completeItems, incompleteItems, type Review } from "./review";
import { type Rubric, type Severity, VERDICTS, type Verdict } from "./rubric";
import {
  cohenKappa,
  fleissKappa,
  type Interval,
  interpretKappa,
  type KappaBand,
  median,
  type WilsonOptions,
  wilson,
} from "./stats";

/** Below this many complete items kappa is reported as `null` with a reason. */
export const DEFAULT_MIN_KAPPA_ITEMS = 10;

/** Options for {@link agreementReport}. */
export interface AgreementOptions {
  /** Rubric version to report on. Default: `rubric.version`. Reviews of other versions are excluded. */
  rubricVersion?: string;
  /** Minimum complete items before any kappa is computed. Default {@link DEFAULT_MIN_KAPPA_ITEMS}. */
  minItems?: number;
  /** Options forwarded to every Wilson interval in the report. */
  wilson?: WilsonOptions;
}

/** A kappa value with its sample size, its Landis & Koch band and, when null, why. */
export interface KappaStat {
  /** Kappa, or `null` when it was not computed or is undefined. */
  value: number | null;
  /** Landis & Koch band of `value`. */
  band: KappaBand | null;
  /** Number of complete items the value is computed over. */
  n: number;
  /** Present when `value` is `null`. */
  reason?: string;
}

/** Agreement between two lenses over the complete items. */
export interface LensPairStat {
  /** First lens (rows of `confusion`). */
  a: string;
  /** Second lens (columns of `confusion`). */
  b: string;
  /** Share of items where both lenses gave the same verdict. */
  agreement: Interval;
  /** Cohen's kappa between the two lenses. */
  cohenKappa: KappaStat;
  /** `confusion[verdictOfA][verdictOfB]` = number of items. */
  confusion: Record<Verdict, Record<Verdict, number>>;
}

/** One lens verdict inside a {@link Disagreement}. */
export interface DisagreementVerdict {
  /** Lens. */
  lens: string;
  /** Verdict given through it. */
  verdict: Verdict;
  /** Error class, if any. */
  errorClass?: string;
  /** Severity, if any. */
  severity?: Severity;
  /** Reviewer note, if any. */
  note?: string;
  /** Who gave it. */
  reviewer: string;
  /** Id of the review. */
  reviewId: string;
}

/** A complete item whose current lens verdicts are not all the same. */
export interface Disagreement {
  /** The item id. */
  itemId: string;
  /** Current verdict per lens, in rubric lens order. */
  verdicts: DisagreementVerdict[];
}

/** Output of {@link agreementReport}. */
export interface AgreementReport {
  /** The rubric version the report is restricted to. */
  rubricVersion: string;
  /** Lenses, in rubric order. */
  lenses: string[];
  /** Complete items (every lens has a current verdict). Only these enter the statistics. */
  items: number;
  /** Items with some but not all lens verdicts, in this rubric version. */
  incompleteItems: number;
  /** Reviews skipped because they were written against another rubric version. */
  excludedReviews: number;
  /** Share of complete items where every lens agrees. */
  rawAgreement: Interval;
  /** Fleiss' kappa with lenses as raters. */
  fleissKappa: KappaStat;
  /** Per lens pair: agreement, Cohen's kappa and a confusion matrix. */
  pairs: LensPairStat[];
  /** Share of current verdicts that are PASS, FAIL and NEEDS_REVIEW (n = items × lenses). */
  prevalence: Record<Verdict, Interval>;
  /** Median `seconds` over all reviews in this version that recorded a time. */
  medianSeconds: number | null;
  /** Median `seconds` per lens. */
  medianSecondsByLens: Record<string, number | null>;
  /** Complete items where the lenses split, in input order. */
  disagreements: Disagreement[];
  /** One-sentence reading of the kappa using Landis & Koch bands, with caveats. */
  interpretation: string;
}

function emptyConfusion(): Record<Verdict, Record<Verdict, number>> {
  const row = (): Record<Verdict, number> => ({ PASS: 0, FAIL: 0, NEEDS_REVIEW: 0 });
  return { PASS: row(), FAIL: row(), NEEDS_REVIEW: row() };
}

function kappaStat(
  value: number | null,
  n: number,
  minItems: number,
  undefinedReason: string,
): KappaStat {
  if (n < minItems) {
    return {
      value: null,
      band: null,
      n,
      reason: `${n} complete item${n === 1 ? "" : "s"}; kappa needs at least ${minItems}`,
    };
  }
  if (value === null) return { value: null, band: null, n, reason: undefinedReason };
  return { value, band: interpretKappa(value), n };
}

/**
 * Builds the agreement report for one rubric version.
 *
 * Only reviews stamped with that version count; only items where every lens has a current
 * (latest) verdict enter the statistics. Kappa is `null`, with a reason, below `minItems`
 * complete items or when it is undefined (no variation in verdicts).
 *
 * Fleiss' kappa treats the lenses as raters of the same item. That measures whether the
 * lenses converge on one verdict; see docs/statistics.md for what that does and does not mean.
 */
export function agreementReport(
  reviews: readonly Review[],
  rubric: Rubric,
  options: AgreementOptions = {},
): AgreementReport {
  const version = options.rubricVersion ?? rubric.version;
  const minItems = options.minItems ?? DEFAULT_MIN_KAPPA_ITEMS;
  const lenses = [...rubric.lenses];
  const scoped = reviews.filter((review) => review.rubricVersion === version);
  const items = completeItems(scoped, lenses);
  const n = items.length;

  const counts = items.map((item) => {
    const row: Partial<Record<Verdict, number>> = {};
    for (const review of item.reviews) row[review.verdict] = (row[review.verdict] ?? 0) + 1;
    return row;
  });
  const unanimous = counts.filter((row) => Object.keys(row).length === 1).length;

  const totalVerdicts = n * lenses.length;
  const prevalence = Object.fromEntries(
    VERDICTS.map((verdict) => [
      verdict,
      wilson(
        counts.reduce((acc, row) => acc + (row[verdict] ?? 0), 0),
        totalVerdicts,
        options.wilson,
      ),
    ]),
  ) as Record<Verdict, Interval>;

  const pairs: LensPairStat[] = [];
  for (let i = 0; i < lenses.length; i += 1) {
    for (let j = i + 1; j < lenses.length; j += 1) {
      const a = lenses[i] as string;
      const b = lenses[j] as string;
      const confusion = emptyConfusion();
      const labelsA: string[] = [];
      const labelsB: string[] = [];
      let agreed = 0;
      for (const item of items) {
        const va = item.reviews[i]?.verdict as Verdict;
        const vb = item.reviews[j]?.verdict as Verdict;
        confusion[va][vb] += 1;
        labelsA.push(va);
        labelsB.push(vb);
        if (va === vb) agreed += 1;
      }
      pairs.push({
        a,
        b,
        agreement: wilson(agreed, n, options.wilson),
        cohenKappa: kappaStat(
          n >= minItems ? cohenKappa(labelsA, labelsB) : null,
          n,
          minItems,
          "both lenses used a single identical verdict; kappa is undefined",
        ),
        confusion,
      });
    }
  }

  const fleiss: KappaStat =
    lenses.length < 2
      ? { value: null, band: null, n, reason: "kappa needs at least two lenses" }
      : kappaStat(
          n >= minItems ? fleissKappa(counts) : null,
          n,
          minItems,
          "every verdict is identical; kappa is undefined without variation",
        );

  const timed = scoped.filter(
    (review): review is Review & { seconds: number } =>
      typeof review.seconds === "number" && lenses.includes(review.lens),
  );
  const medianSecondsByLens = Object.fromEntries(
    lenses.map((lens) => [
      lens,
      median(timed.filter((review) => review.lens === lens).map((review) => review.seconds)),
    ]),
  );

  const disagreements: Disagreement[] = items
    .filter((item) => new Set(item.reviews.map((review) => review.verdict)).size > 1)
    .map((item) => ({
      itemId: item.itemId,
      verdicts: item.reviews.map((review) => {
        const verdict: DisagreementVerdict = {
          lens: review.lens,
          verdict: review.verdict,
          reviewer: review.reviewer,
          reviewId: review.id,
        };
        if (review.errorClass !== undefined) verdict.errorClass = review.errorClass;
        if (review.severity !== undefined) verdict.severity = review.severity;
        if (review.note !== undefined) verdict.note = review.note;
        return verdict;
      }),
    }));

  return {
    rubricVersion: version,
    lenses,
    items: n,
    incompleteItems: incompleteItems(scoped, lenses).length,
    excludedReviews: reviews.length - scoped.length,
    rawAgreement: wilson(unanimous, n, options.wilson),
    fleissKappa: fleiss,
    pairs,
    prevalence,
    medianSeconds: median(timed.map((review) => review.seconds)),
    medianSecondsByLens,
    disagreements,
    interpretation: interpret(fleiss, lenses.length, prevalence),
  };
}

function interpret(
  fleiss: KappaStat,
  lensCount: number,
  prevalence: Record<Verdict, Interval>,
): string {
  if (fleiss.value === null) return `Kappa not reported: ${fleiss.reason}.`;
  const parts = [
    `Fleiss' kappa ${fleiss.value.toFixed(2)} across ${lensCount} lenses on ${fleiss.n} complete items: ${fleiss.band} agreement (Landis & Koch).`,
  ];
  const dominant = VERDICTS.find((verdict) => prevalence[verdict].p >= 0.8);
  if (dominant) {
    parts.push(
      `${dominant} is ${Math.round(prevalence[dominant].p * 100)}% of verdicts; kappa is depressed when one verdict dominates, so read raw agreement alongside it.`,
    );
  }
  if (fleiss.n < 50) {
    parts.push(`With ${fleiss.n} items the kappa itself is noisy; treat the band as provisional.`);
  }
  return parts.join(" ");
}
