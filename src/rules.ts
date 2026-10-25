/**
 * A small, generic rule engine for scoring an output against a golden case's expectations.
 * Rules address a value in the output with a dot path and apply one operator. `custom()`
 * covers anything the operators cannot express.
 *
 * @module
 */

import { err, isRecord, ok, type Result } from "./result";

/** Every built-in operator. */
export const RULE_OPS = [
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
  "exists",
] as const;

/** A built-in operator name. */
export type RuleOp = (typeof RULE_OPS)[number];

/**
 * A declarative, JSON-serialisable rule.
 *
 * | op | `value` | passes when the value at `path` ... |
 * |---|---|---|
 * | `eq` / `neq` | any | deep-equals / does not deep-equal `value` |
 * | `in` / `notIn` | array | is / is not deep-equal to one element |
 * | `between` | `[min, max]` | is a number with `min <= x <= max` |
 * | `contains` / `notContains` | string or string[] | (string) contains every / none of the phrases after {@link normalizeText}; (array) has every / none of them as an element |
 * | `matches` / `notMatches` | regex source | is a string that matches / does not match (`flags` optional) |
 * | `subsetOf` | array | is an array whose every element is in `value` |
 * | `exists` | boolean, default `true` | is present (`true`) or absent (`false`) |
 *
 * A path that does not resolve fails every operator except `exists`.
 */
export interface PathRule {
  /** Dot path into the output, e.g. `"reply"`, `"actions.0"`, `"meta.flags[1]"`. `""` or `"$"` is the root. */
  path: string;
  /** Operator. */
  op: RuleOp;
  /** Operand, see the table above. */
  value?: unknown;
  /** Regex flags for `matches` / `notMatches`, e.g. `"i"`. */
  flags?: string;
  /** Failure code reported when the rule fails. Default `` `${op}(${path})` ``. */
  code?: string;
}

/** What a custom check returns: `true` passes; `false` or a string (the detail) fails. */
export type CustomCheckResult = boolean | string;

/** A rule implemented as a function. Not serialisable: supply it at scoring time. */
export interface CustomRule {
  /** Discriminator. */
  op: "custom";
  /** Failure code reported when the rule fails. */
  code: string;
  /** Optional path reported in the failure, for display only. */
  path?: string;
  /** The check. Exceptions are caught and reported as failures. */
  check: (output: unknown) => CustomCheckResult;
}

/** Any rule {@link scoreOutput} accepts. */
export type Rule = PathRule | CustomRule;

/** One failed rule. */
export interface RuleFailure {
  /** The rule's code. */
  code: string;
  /** The rule's path (`""` for custom rules without one). */
  path: string;
  /** Human-readable reason. */
  detail: string;
}

/** Output of {@link scoreOutput}. */
export interface ScoreResult {
  /** `true` when no rule failed. */
  passed: boolean;
  /** Every failed rule, in rule order. */
  failures: RuleFailure[];
}

/** Builds a {@link CustomRule}. */
export function custom(
  code: string,
  check: (output: unknown) => CustomCheckResult,
  path?: string,
): CustomRule {
  const rule: CustomRule = { op: "custom", code, check };
  if (path !== undefined) rule.path = path;
  return rule;
}

/**
 * Case- and whitespace-insensitive text folding used by `contains` / `notContains`:
 * lower-cases, strips diacritics (NFKD), turns punctuation into spaces and collapses runs of
 * whitespace. `"Full  REFUND, guaranteed!"` becomes `"full refund guaranteed"`.
 */
export function normalizeText(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * `true` when `needle` occurs in `haystack` as a whole phrase after {@link normalizeText}:
 * `"refund"` is found in `"Your refund is on its way"` but not in `"refundable"`.
 */
export function containsPhrase(haystack: string, needle: string): boolean {
  const n = normalizeText(needle);
  if (!n) return true;
  return ` ${normalizeText(haystack)} `.includes(` ${n} `);
}

function splitPath(path: string): string[] {
  if (path === "" || path === "$") return [];
  const trimmed = path.startsWith("$.") ? path.slice(2) : path;
  return trimmed
    .replace(/\[(\d+)\]/g, ".$1")
    .split(".")
    .filter((segment) => segment.length > 0);
}

/**
 * Resolves a dot path. Numeric segments index arrays; `a[0]` is accepted as `a.0`.
 * Only own properties are followed.
 */
export function getPath(value: unknown, path: string): { found: boolean; value: unknown } {
  let current: unknown = value;
  for (const segment of splitPath(path)) {
    if (Array.isArray(current)) {
      if (!/^\d+$/.test(segment)) return { found: false, value: undefined };
      const index = Number(segment);
      if (index >= current.length) return { found: false, value: undefined };
      current = current[index];
    } else if (isRecord(current)) {
      if (!Object.hasOwn(current, segment)) return { found: false, value: undefined };
      current = current[segment];
    } else {
      return { found: false, value: undefined };
    }
  }
  return { found: true, value: current };
}

/** Structural equality for JSON-like values (primitives, arrays, plain objects). */
export function deepEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, i) => deepEqual(item, b[i]));
  }
  if (isRecord(a) && isRecord(b)) {
    const keys = Object.keys(a);
    if (keys.length !== Object.keys(b).length) return false;
    return keys.every((key) => Object.hasOwn(b, key) && deepEqual(a[key], b[key]));
  }
  return false;
}

function show(value: unknown): string {
  let text: string;
  try {
    text = value === undefined ? "undefined" : (JSON.stringify(value) ?? String(value));
  } catch {
    text = String(value);
  }
  return text.length > 80 ? `${text.slice(0, 77)}...` : text;
}

function phrases(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [value];
}

function elementMatches(element: unknown, wanted: unknown): boolean {
  if (typeof element === "string" && typeof wanted === "string") {
    return normalizeText(element) === normalizeText(wanted);
  }
  return deepEqual(element, wanted);
}

function regex(rule: PathRule): RegExp {
  if (rule.value instanceof RegExp) return rule.value;
  return new RegExp(String(rule.value), rule.flags);
}

function checkPathRule(output: unknown, rule: PathRule): string | null {
  const resolved = getPath(output, rule.path);
  const where = rule.path || "$";
  if (rule.op === "exists") {
    const wanted = rule.value === undefined ? true : Boolean(rule.value);
    if (resolved.found === wanted) return null;
    return wanted
      ? `${where} is missing`
      : `${where} should be absent but is ${show(resolved.value)}`;
  }
  if (!resolved.found) return `${where} is missing`;
  const actual = resolved.value;
  switch (rule.op) {
    case "eq":
      return deepEqual(actual, rule.value)
        ? null
        : `expected ${show(rule.value)}, got ${show(actual)}`;
    case "neq":
      return deepEqual(actual, rule.value) ? `must not equal ${show(rule.value)}` : null;
    case "in": {
      const options = Array.isArray(rule.value) ? rule.value : [];
      return options.some((option) => deepEqual(actual, option))
        ? null
        : `${show(actual)} is not one of ${show(options)}`;
    }
    case "notIn": {
      const options = Array.isArray(rule.value) ? rule.value : [];
      return options.some((option) => deepEqual(actual, option))
        ? `${show(actual)} is one of the excluded values ${show(options)}`
        : null;
    }
    case "between": {
      const [min, max] = Array.isArray(rule.value) ? rule.value : [];
      if (typeof actual !== "number" || !Number.isFinite(actual)) {
        return `expected a number, got ${show(actual)}`;
      }
      return actual >= Number(min) && actual <= Number(max)
        ? null
        : `${actual} is outside [${String(min)}, ${String(max)}]`;
    }
    case "contains":
    case "notContains": {
      const wanted = phrases(rule.value);
      let present: unknown[];
      if (typeof actual === "string") {
        present = wanted.filter(
          (phrase) => typeof phrase === "string" && containsPhrase(actual, phrase),
        );
      } else if (Array.isArray(actual)) {
        present = wanted.filter((phrase) =>
          actual.some((element) => elementMatches(element, phrase)),
        );
      } else {
        return `expected a string or an array, got ${show(actual)}`;
      }
      if (rule.op === "contains") {
        const absent = wanted.filter((phrase) => !present.includes(phrase));
        return absent.length === 0 ? null : `missing ${absent.map(show).join(", ")}`;
      }
      return present.length === 0 ? null : `contains forbidden ${present.map(show).join(", ")}`;
    }
    case "matches":
    case "notMatches": {
      if (typeof actual !== "string") return `expected a string, got ${show(actual)}`;
      const pattern = regex(rule);
      const hit = pattern.test(actual);
      pattern.lastIndex = 0;
      if (rule.op === "matches")
        return hit ? null : `does not match /${pattern.source}/${pattern.flags}`;
      return hit ? `matches forbidden /${pattern.source}/${pattern.flags}` : null;
    }
    case "subsetOf": {
      if (!Array.isArray(actual)) return `expected an array, got ${show(actual)}`;
      const allowed = Array.isArray(rule.value) ? rule.value : [];
      const extra = actual.filter(
        (element) => !allowed.some((option) => elementMatches(element, option)),
      );
      return extra.length === 0 ? null : `not allowed: ${extra.map(show).join(", ")}`;
    }
  }
  return `unknown operator ${show((rule as { op: unknown }).op)}`;
}

/** Evaluates one rule. Returns the failure, or `null` when the rule passes. */
export function evaluateRule(output: unknown, rule: Rule): RuleFailure | null {
  if (rule.op === "custom") {
    let result: CustomCheckResult;
    try {
      result = rule.check(output);
    } catch (error) {
      result = `custom rule threw: ${error instanceof Error ? error.message : String(error)}`;
    }
    if (result === true) return null;
    return {
      code: rule.code,
      path: rule.path ?? "",
      detail: typeof result === "string" ? result : "custom rule failed",
    };
  }
  const detail = checkPathRule(output, rule);
  if (detail === null) return null;
  return { code: rule.code ?? `${rule.op}(${rule.path || "$"})`, path: rule.path, detail };
}

/**
 * Scores an output against rules. Every rule is evaluated (no short-circuit) so the result
 * lists every failure.
 *
 * @example
 * scoreOutput({ reply: "We guarantee a refund." }, [
 *   { path: "reply", op: "notContains", value: ["guarantee"], code: "FORBIDDEN_PROMISE" },
 * ]) // { passed: false, failures: [{ code: "FORBIDDEN_PROMISE", path: "reply", detail: ... }] }
 */
export function scoreOutput(output: unknown, rules: readonly Rule[]): ScoreResult {
  const failures: RuleFailure[] = [];
  for (const rule of rules) {
    const failure = evaluateRule(output, rule);
    if (failure) failures.push(failure);
  }
  return { passed: failures.length === 0, failures };
}

/**
 * Validates untrusted input (for example `expectedRules` from a JSON file) as a list of
 * {@link PathRule}. Checks operator names and operand shapes and compiles every regex.
 */
export function parseRules(input: unknown): Result<PathRule[], string[]> {
  if (!Array.isArray(input)) return err(["rules must be an array"]);
  const issues: string[] = [];
  const rules: PathRule[] = [];
  input.forEach((raw, i) => {
    const at = `rules[${i}]`;
    if (!isRecord(raw)) {
      issues.push(`${at} must be an object`);
      return;
    }
    const before = issues.length;
    if (typeof raw.path !== "string") issues.push(`${at}.path must be a string`);
    const op = raw.op;
    if (typeof op !== "string" || !(RULE_OPS as readonly string[]).includes(op)) {
      issues.push(`${at}.op must be one of ${RULE_OPS.join(", ")}`);
    } else {
      const value = raw.value;
      if ((op === "in" || op === "notIn" || op === "subsetOf") && !Array.isArray(value)) {
        issues.push(`${at}: ${op} needs an array value`);
      }
      if (op === "between") {
        const ok =
          Array.isArray(value) &&
          value.length === 2 &&
          value.every((bound) => typeof bound === "number" && Number.isFinite(bound)) &&
          (value[0] as number) <= (value[1] as number);
        if (!ok) issues.push(`${at}: between needs [min, max] numbers with min <= max`);
      }
      if (op === "contains" || op === "notContains") {
        const list = Array.isArray(value) ? value : [value];
        if (list.length === 0 || value === undefined) issues.push(`${at}: ${op} needs a value`);
        if (list.some((phrase) => typeof phrase === "string" && normalizeText(phrase) === "")) {
          issues.push(`${at}: ${op} phrases must contain a letter or digit`);
        }
      }
      if (op === "matches" || op === "notMatches") {
        if (typeof value !== "string") {
          issues.push(`${at}: ${op} needs a regex source string`);
        } else {
          try {
            new RegExp(value, typeof raw.flags === "string" ? raw.flags : undefined);
          } catch (error) {
            issues.push(
              `${at}: invalid regex (${error instanceof Error ? error.message : String(error)})`,
            );
          }
        }
      }
      if (op === "exists" && value !== undefined && typeof value !== "boolean") {
        issues.push(`${at}: exists takes an optional boolean`);
      }
    }
    if (raw.flags !== undefined && typeof raw.flags !== "string")
      issues.push(`${at}.flags must be a string`);
    if (raw.code !== undefined && (typeof raw.code !== "string" || raw.code === "")) {
      issues.push(`${at}.code must be a non-empty string`);
    }
    if (issues.length === before) {
      const rule: PathRule = { path: raw.path as string, op: op as RuleOp };
      if ("value" in raw) rule.value = raw.value;
      if (typeof raw.flags === "string") rule.flags = raw.flags;
      if (typeof raw.code === "string") rule.code = raw.code;
      rules.push(rule);
    }
  });
  return issues.length > 0 ? err(issues) : ok(rules);
}
