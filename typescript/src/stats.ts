/**
 * Small, dependency-free statistics. Every rate this module produces carries its
 * interval and its n: there is no function here that returns a bare percentage.
 *
 * @module
 */

/** A proportion with its Wilson score interval, its sample size and a decidability flag. */
export interface Interval {
  /** Point estimate `k / n`, in [0, 1]. `0` when `n` is 0. */
  p: number;
  /** Lower bound of the interval, clamped to [0, 1]. */
  low: number;
  /** Upper bound of the interval, clamped to [0, 1]. */
  high: number;
  /** Number of successes the estimate is built from. */
  k: number;
  /** Number of trials the estimate is built from. */
  n: number;
  /**
   * `true` only when `n >= minN` and `high - low <= maxWidth`. A rate that is not
   * decidable may be printed, but it must not decide a gate.
   */
  decidable: boolean;
}

/** Options for {@link wilson} and {@link formatRate}. */
export interface WilsonOptions {
  /** Standard normal quantile. Default `1.96` (a two-sided 95% interval). */
  z?: number;
  /** Below this many trials a rate is never decidable. Default {@link DEFAULT_MIN_N}. */
  minN?: number;
  /** An interval wider than this (high - low) is never decidable. Default {@link DEFAULT_MAX_WIDTH}. */
  maxWidth?: number;
}

/** Default z for a two-sided 95% interval. */
export const DEFAULT_Z = 1.96;
/** Below this many trials a rate never decides a gate. */
export const DEFAULT_MIN_N = 50;
/** A 95% interval wider than this (high - low) is too loose to act on. */
export const DEFAULT_MAX_WIDTH = 0.25;

/**
 * Wilson score interval for a binomial proportion.
 *
 * `passed` is clamped to `[0, total]`. A non-finite or non-positive `total` returns an
 * empty, undecidable interval (`n = 0`).
 *
 * @example
 * wilson(12, 20) // { p: 0.6, low: 0.387, high: 0.781, k: 12, n: 20, decidable: false }
 */
export function wilson(passed: number, total: number, options: WilsonOptions = {}): Interval {
  const z = options.z ?? DEFAULT_Z;
  const minN = options.minN ?? DEFAULT_MIN_N;
  const maxWidth = options.maxWidth ?? DEFAULT_MAX_WIDTH;
  if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(passed)) {
    return { p: 0, low: 0, high: 0, k: 0, n: 0, decidable: false };
  }
  const k = Math.min(Math.max(passed, 0), total);
  const p = k / total;
  const z2 = z * z;
  const denom = 1 + z2 / total;
  const center = (p + z2 / (2 * total)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / total + z2 / (4 * total * total))) / denom;
  const low = Math.max(0, center - half);
  const high = Math.min(1, center + half);
  return { p, low, high, k, n: total, decidable: total >= minN && high - low <= maxWidth };
}

/** Rounds a proportion to a whole percentage string, e.g. `0.604 -> "60%"`. */
export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/**
 * Formats an {@link Interval} so that the rate, the counts and the interval always travel
 * together, e.g. `"60% (12/20) · 39%–78% · not decidable"`.
 */
export function formatInterval(interval: Interval): string {
  if (interval.n === 0) return "no data · not decidable";
  const base = `${percent(interval.p)} (${interval.k}/${interval.n}) · ${percent(interval.low)}–${percent(interval.high)}`;
  return interval.decidable ? base : `${base} · not decidable`;
}

/**
 * Computes the Wilson interval for `passed / total` and formats it with
 * {@link formatInterval}.
 *
 * @example
 * formatRate(12, 20)  // "60% (12/20) · 39%–78% · not decidable"
 * formatRate(80, 100) // "80% (80/100) · 71%–87%"
 */
export function formatRate(passed: number, total: number, options: WilsonOptions = {}): string {
  return formatInterval(wilson(passed, total, options));
}

/** Median of the finite values in `values`; `null` when there are none. Does not mutate the input. */
export function median(values: readonly number[]): number | null {
  const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  const mid = Math.floor(sorted.length / 2);
  const upper = sorted[mid] as number;
  return sorted.length % 2 ? upper : ((sorted[mid - 1] as number) + upper) / 2;
}

function sum(values: Iterable<number | undefined>): number {
  let total = 0;
  for (const value of values) total += value ?? 0;
  return total;
}

/**
 * Fleiss' kappa for a fixed number of raters per subject.
 *
 * Each row maps a category to the number of raters who chose it for one subject. The rater
 * count is taken from the first row; rows with a different total are ignored. Returns `null`
 * when there are no usable rows, fewer than two raters, or when chance agreement is total
 * (every rating falls in one category), where kappa is undefined.
 *
 * @example
 * fleissKappa([{ PASS: 3 }, { PASS: 2, FAIL: 1 }, { FAIL: 3 }])
 */
export function fleissKappa(
  rows: ReadonlyArray<Readonly<Record<string, number | undefined>>>,
): number | null {
  const first = rows[0];
  if (!first) return null;
  const raters = sum(Object.values(first));
  if (raters < 2) return null;
  const usable = rows.filter((row) => sum(Object.values(row)) === raters);
  if (usable.length === 0) return null;
  const categories = [...new Set(usable.flatMap((row) => Object.keys(row)))];
  const subjects = usable.length;
  const pBar =
    sum(
      usable.map((row) => {
        const squares = sum(Object.values(row).map((count) => (count ?? 0) * (count ?? 0)));
        return (squares - raters) / (raters * (raters - 1));
      }),
    ) / subjects;
  const pE = sum(
    categories.map((category) => {
      const pj = sum(usable.map((row) => row[category] ?? 0)) / (subjects * raters);
      return pj * pj;
    }),
  );
  if (1 - pE <= 1e-12) return null;
  return (pBar - pE) / (1 - pE);
}

/**
 * Cohen's kappa for two raters who labelled the same subjects in the same order.
 *
 * Returns `null` for empty input or when expected agreement is total (both raters used a
 * single, identical category), where kappa is undefined.
 *
 * @throws {RangeError} when `a` and `b` have different lengths.
 */
export function cohenKappa(a: readonly string[], b: readonly string[]): number | null {
  if (a.length !== b.length) {
    throw new RangeError(
      `cohenKappa: rater label arrays differ in length (${a.length} vs ${b.length})`,
    );
  }
  const n = a.length;
  if (n === 0) return null;
  const countsA = new Map<string, number>();
  const countsB = new Map<string, number>();
  let agreed = 0;
  for (let i = 0; i < n; i += 1) {
    const x = a[i] as string;
    const y = b[i] as string;
    if (x === y) agreed += 1;
    countsA.set(x, (countsA.get(x) ?? 0) + 1);
    countsB.set(y, (countsB.get(y) ?? 0) + 1);
  }
  const pO = agreed / n;
  let pE = 0;
  for (const [category, count] of countsA) pE += (count / n) * ((countsB.get(category) ?? 0) / n);
  if (1 - pE <= 1e-12) return null;
  return (pO - pE) / (1 - pE);
}

/** Landis & Koch (1977) agreement bands. */
export type KappaBand = "poor" | "slight" | "fair" | "moderate" | "substantial" | "almost perfect";

/**
 * Maps a kappa to its Landis & Koch (1977) band: `< 0` poor, `0–0.20` slight,
 * `0.21–0.40` fair, `0.41–0.60` moderate, `0.61–0.80` substantial, `0.81–1` almost perfect.
 * Returns `null` for `null` or non-finite input.
 */
export function interpretKappa(kappa: number | null): KappaBand | null {
  if (kappa === null || !Number.isFinite(kappa)) return null;
  if (kappa < 0) return "poor";
  if (kappa <= 0.2) return "slight";
  if (kappa <= 0.4) return "fair";
  if (kappa <= 0.6) return "moderate";
  if (kappa <= 0.8) return "substantial";
  return "almost perfect";
}
