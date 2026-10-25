/**
 * Rubrics: the versioned contract reviewers grade against. A rubric declares its lenses and
 * the error classes each lens may pick. Every review is stamped with the rubric version, and
 * agreement is only ever computed within one version.
 *
 * @module
 */

import { err, isRecord, ok, type Result } from "./result";

/** The three verdicts a reviewer can give. */
export const VERDICTS = ["PASS", "FAIL", "NEEDS_REVIEW"] as const;

/** `PASS | FAIL | NEEDS_REVIEW`. */
export type Verdict = (typeof VERDICTS)[number];

/** Severities a failing review may carry. */
export const SEVERITIES = ["low", "medium", "high"] as const;

/** `low | medium | high`. */
export type Severity = (typeof SEVERITIES)[number];

/** `true` when `value` is one of {@link VERDICTS}. */
export function isVerdict(value: unknown): value is Verdict {
  return typeof value === "string" && (VERDICTS as readonly string[]).includes(value);
}

/** `true` when `value` is one of {@link SEVERITIES}. */
export function isSeverity(value: unknown): value is Severity {
  return typeof value === "string" && (SEVERITIES as readonly string[]).includes(value);
}

/**
 * One error class a reviewer may assign to a failing output. `include` and `exclude` are
 * the decision rules; examples are optional but strongly recommended.
 */
export interface ErrorClassDef<L extends string = string, C extends string = string> {
  /** Stable machine code, unique within the rubric, e.g. `"WRONG_FACT"`. */
  readonly code: C;
  /** Short human label. */
  readonly label: string;
  /** Lenses that may pick this class. Must be declared in the rubric. */
  readonly lenses: readonly L[];
  /** When to pick this class. */
  readonly include: string;
  /** When not to pick it (and what to pick instead). */
  readonly exclude: string;
  /** An output that is fine with respect to this class. */
  readonly goodExample?: string;
  /** An output that shows this error. */
  readonly badExample?: string;
}

/** A validated, frozen rubric. Create it with {@link defineRubric}. */
export interface Rubric<L extends string = string, C extends string = string> {
  /** Version label. Bump it whenever an include/exclude rule changes meaning. */
  readonly version: string;
  /** Reviewer perspectives. An item is complete when every lens has a current verdict. */
  readonly lenses: readonly L[];
  /** Error classes reviewers may assign. */
  readonly errorClasses: readonly ErrorClassDef<L, C>[];
}

/** Input to {@link defineRubric}. */
export interface RubricInput<L extends string, C extends string> {
  version: string;
  lenses: readonly L[];
  errorClasses: readonly ErrorClassDef<NoInfer<L>, C>[];
}

/** Thrown by {@link defineRubric} when the rubric is invalid. `issues` lists every problem. */
export class RubricError extends Error {
  /** Every validation problem found, in order. */
  readonly issues: readonly string[];

  constructor(issues: readonly string[]) {
    super(`Invalid rubric: ${issues.join("; ")}`);
    this.name = "RubricError";
    this.issues = issues;
  }
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function collectIssues(input: unknown): string[] {
  const issues: string[] = [];
  if (!isRecord(input)) return ["rubric must be an object"];
  if (!nonEmptyString(input.version)) issues.push("version must be a non-empty string");
  const lenses = input.lenses;
  const declared = new Set<string>();
  if (!Array.isArray(lenses) || lenses.length === 0) {
    issues.push("lenses must be a non-empty array");
  } else {
    lenses.forEach((lens, i) => {
      if (!nonEmptyString(lens)) issues.push(`lenses[${i}] must be a non-empty string`);
      else if (declared.has(lens)) issues.push(`lens "${lens}" is declared twice`);
      else declared.add(lens);
    });
  }
  const classes = input.errorClasses;
  if (!Array.isArray(classes)) {
    issues.push("errorClasses must be an array");
    return issues;
  }
  const codes = new Set<string>();
  classes.forEach((entry, i) => {
    const at = `errorClasses[${i}]`;
    if (!isRecord(entry)) {
      issues.push(`${at} must be an object`);
      return;
    }
    if (!nonEmptyString(entry.code)) issues.push(`${at}.code must be a non-empty string`);
    else if (codes.has(entry.code)) issues.push(`error class code "${entry.code}" is not unique`);
    else codes.add(entry.code);
    const name = nonEmptyString(entry.code) ? `error class "${entry.code}"` : at;
    for (const field of ["label", "include", "exclude"] as const) {
      if (!nonEmptyString(entry[field]))
        issues.push(`${name}: ${field} must be a non-empty string`);
    }
    for (const field of ["goodExample", "badExample"] as const) {
      if (entry[field] !== undefined && typeof entry[field] !== "string") {
        issues.push(`${name}: ${field} must be a string when present`);
      }
    }
    if (!Array.isArray(entry.lenses) || entry.lenses.length === 0) {
      issues.push(`${name}: lenses must be a non-empty array`);
    } else {
      for (const lens of entry.lenses) {
        if (typeof lens !== "string" || !declared.has(lens)) {
          issues.push(`${name}: references undeclared lens "${String(lens)}"`);
        }
      }
    }
  });
  return issues;
}

function freezeRubric<L extends string, C extends string>(input: RubricInput<L, C>): Rubric<L, C> {
  const errorClasses = input.errorClasses.map((entry) =>
    Object.freeze({ ...entry, lenses: Object.freeze([...entry.lenses]) }),
  );
  return Object.freeze({
    version: input.version,
    lenses: Object.freeze([...input.lenses]),
    errorClasses: Object.freeze(errorClasses),
  });
}

/**
 * Validates a rubric and returns a frozen, typed copy. Lens names and error-class codes are
 * inferred as literal types.
 *
 * Checks: non-empty version; non-empty, unique lenses; unique, non-empty error-class codes;
 * non-empty label/include/exclude; every error class references declared lenses only.
 *
 * @throws {RubricError} listing every issue when the rubric is invalid.
 *
 * @example
 * const rubric = defineRubric({
 *   version: "2026-10.1",
 *   lenses: ["accuracy", "tone"],
 *   errorClasses: [
 *     { code: "WRONG_FACT", label: "Wrong fact", lenses: ["accuracy"],
 *       include: "States something false.", exclude: "Merely incomplete." },
 *   ],
 * });
 */
export function defineRubric<const L extends string, const C extends string>(
  input: RubricInput<L, C>,
): Rubric<L, C> {
  const issues = collectIssues(input);
  if (issues.length > 0) throw new RubricError(issues);
  return freezeRubric(input);
}

/**
 * Validates untrusted input (for example a parsed `rubric.json`) and returns a Result instead
 * of throwing. Use {@link defineRubric} for rubrics written in code.
 */
export function parseRubric(input: unknown): Result<Rubric, string[]> {
  const issues = collectIssues(input);
  if (issues.length > 0) return err(issues);
  return ok(freezeRubric(input as RubricInput<string, string>));
}

/** Error classes the given lens may pick, in rubric order. */
export function errorClassesForLens<L extends string, C extends string>(
  rubric: Rubric<L, C>,
  lens: L,
): ErrorClassDef<L, C>[] {
  return rubric.errorClasses.filter((entry) => entry.lenses.includes(lens));
}

/** The error class with this code, or `undefined`. */
export function findErrorClass<L extends string, C extends string>(
  rubric: Rubric<L, C>,
  code: string,
): ErrorClassDef<L, C> | undefined {
  return rubric.errorClasses.find((entry) => entry.code === code);
}
