/**
 * Calibration: versioned configuration under test (prompt, model, thresholds, rule sets).
 * A change is a draft with a reason; activating it supersedes the active version; rolling back
 * restores the most recent superseded one. Each step can write an audit event.
 *
 * @module
 */

import type { AuditEvent, AuditSink } from "./audit";
import { err, isRecord, ok, type Result } from "./result";
import { deepEqual } from "./rules";
import type { CalibrationStore } from "./store/types";

/** Lifecycle of a calibration version. */
export type CalibrationStatus = "draft" | "active" | "superseded" | "rolled_back";

/** One version of a configuration. */
export interface CalibrationVersion<T = unknown> {
  /** Version id. */
  id: string;
  /** Monotonic version number, starting at 1. */
  version: number;
  /** Current status. */
  status: CalibrationStatus;
  /** The configuration itself, as a frozen {@link snapshot}. */
  config: T;
  /** Why this version exists. */
  reason: string;
  /** Who drafted it. */
  author: string;
  /** ISO-8601 creation time. */
  createdAt: string;
  /** The version that was active when this draft was created, or `null`. */
  baseVersionId: string | null;
  /** When it was activated, if ever. */
  activatedAt?: string;
  /** Who activated it, if ever. */
  activatedBy?: string;
}

/** Why a calibration step was refused. */
export interface CalibrationRefusal {
  /** Machine code. */
  code:
    | "REASON_REQUIRED"
    | "ACTOR_REQUIRED"
    | "NOT_FOUND"
    | "NOT_DRAFT"
    | "SELF_ACTIVATION"
    | "NO_ACTIVE"
    | "NO_PREVIOUS";
  /** Human-readable explanation. */
  message: string;
}

function sortKeys(value: unknown, seen: Set<unknown>): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError("snapshot: numbers must be finite");
    return value;
  }
  if (typeof value !== "object") {
    throw new TypeError(`snapshot: ${typeof value} is not JSON-serialisable`);
  }
  if (seen.has(value)) throw new TypeError("snapshot: circular reference");
  seen.add(value);
  let out: unknown;
  if (Array.isArray(value)) {
    out = Object.freeze(value.map((item) => sortKeys(item, seen)));
  } else if (
    isRecord(value) &&
    (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)
  ) {
    const entries = Object.keys(value)
      .sort()
      .filter((key) => value[key] !== undefined)
      .map((key) => [key, sortKeys(value[key], seen)] as const);
    out = Object.freeze(Object.fromEntries(entries));
  } else {
    throw new TypeError("snapshot: only plain objects, arrays and JSON primitives are allowed");
  }
  seen.delete(value);
  return out;
}

/**
 * A deep, key-sorted, deeply frozen copy of a JSON-like configuration. Two equal configs
 * produce snapshots with identical `JSON.stringify` output.
 *
 * @throws {TypeError} for values that are not plain JSON (functions, class instances, cycles,
 * non-finite numbers).
 */
export function snapshot<T>(config: T): T {
  return sortKeys(config, new Set()) as T;
}

function formatValue(value: unknown): string {
  if (value === undefined) return "(absent)";
  const text = JSON.stringify(value) ?? String(value);
  return text.length > 60 ? `${text.slice(0, 57)}...` : text;
}

/**
 * Human-readable lines describing how `after` differs from `before`, one per changed leaf:
 * `"retry.max: 3 → 5"`, `"model: \"a\" → \"b\""`, `"stop: (absent) → [\"\\n\"]"`.
 * Objects are walked key by key; arrays and primitives are compared whole. Returns `[]` when
 * the values are structurally equal.
 */
export function diffValues(before: unknown, after: unknown, path = ""): string[] {
  if (isRecord(before) && isRecord(after)) {
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort();
    return keys.flatMap((key) =>
      diffValues(before[key], after[key], path ? `${path}.${key}` : key),
    );
  }
  if (deepEqual(before, after)) return [];
  return [`${path || "(root)"}: ${formatValue(before)} → ${formatValue(after)}`];
}

/** Input to {@link createDraft}. */
export interface DraftInput<T> {
  /** The configuration to version. Snapshotted. */
  config: T;
  /** Why. Required. */
  reason: string;
  /** Who. Required. */
  author: string;
  /** Version id. Default `cal-<version>`. */
  id?: string;
  /** ISO-8601 time. Default: now. */
  at?: string;
}

/** Input to {@link activate}. */
export interface ActivateInput {
  /** Id of the draft to activate. */
  id: string;
  /** Who activates. Must differ from the author unless `allowSelfActivation`. */
  actor: string;
  /** Why. Required. */
  reason: string;
  /** Allow the draft's author to activate it. Default `false` (four-eyes). */
  allowSelfActivation?: boolean;
  /** ISO-8601 time. Default: now. */
  at?: string;
}

/** Input to {@link rollback}. */
export interface RollbackInput {
  /** Who rolls back. */
  actor: string;
  /** Why. Required. */
  reason: string;
  /** ISO-8601 time. Default: now. */
  at?: string;
}

function refuse(
  code: CalibrationRefusal["code"],
  message: string,
): Result<never, CalibrationRefusal> {
  return err({ code, message });
}

/** The active version, if any. */
export async function activeVersion<T>(
  store: CalibrationStore<T>,
): Promise<CalibrationVersion<T> | undefined> {
  return (await store.listVersions()).find((version) => version.status === "active");
}

/**
 * Stores a new draft version. The draft records the currently active version as its base so
 * the change can be reviewed with `diffValues(base.config, draft.config)`.
 */
export async function createDraft<T>(
  store: CalibrationStore<T>,
  input: DraftInput<T>,
  audit?: AuditSink,
): Promise<Result<CalibrationVersion<T>, CalibrationRefusal>> {
  const reason = input.reason.trim();
  if (!reason) return refuse("REASON_REQUIRED", "a draft needs a reason");
  if (!input.author.trim()) return refuse("ACTOR_REQUIRED", "a draft needs an author");
  const versions = await store.listVersions();
  const next = versions.reduce((max, version) => Math.max(max, version.version), 0) + 1;
  const active = versions.find((version) => version.status === "active");
  const draft: CalibrationVersion<T> = {
    id: input.id ?? `cal-${next}`,
    version: next,
    status: "draft",
    config: snapshot(input.config),
    reason,
    author: input.author,
    createdAt: input.at ?? new Date().toISOString(),
    baseVersionId: active?.id ?? null,
  };
  await store.saveVersion(draft);
  await audit?.append({
    at: draft.createdAt,
    actor: draft.author,
    action: "calibration.draft",
    target: draft.id,
    reason,
    after: { version: draft.version, status: "draft", baseVersionId: draft.baseVersionId },
  });
  return ok(draft);
}

/**
 * Activates a draft and supersedes the previously active version. By default the author may
 * not activate their own draft.
 */
export async function activate<T>(
  store: CalibrationStore<T>,
  input: ActivateInput,
  audit?: AuditSink,
): Promise<Result<CalibrationVersion<T>, CalibrationRefusal>> {
  const reason = input.reason.trim();
  if (!reason) return refuse("REASON_REQUIRED", "activation needs a reason");
  if (!input.actor.trim()) return refuse("ACTOR_REQUIRED", "activation needs an actor");
  const target = await store.getVersion(input.id);
  if (!target) return refuse("NOT_FOUND", `no calibration version ${input.id}`);
  if (target.status !== "draft") {
    return refuse("NOT_DRAFT", `only a draft can be activated; ${target.id} is ${target.status}`);
  }
  if (target.author === input.actor && !input.allowSelfActivation) {
    return refuse("SELF_ACTIVATION", "the activator must differ from the author");
  }
  const at = input.at ?? new Date().toISOString();
  const previous = await activeVersion(store);
  if (previous) await store.saveVersion({ ...previous, status: "superseded" });
  const activated: CalibrationVersion<T> = {
    ...target,
    status: "active",
    activatedAt: at,
    activatedBy: input.actor,
  };
  await store.saveVersion(activated);
  const event: AuditEvent = {
    at,
    actor: input.actor,
    action: "calibration.activate",
    target: activated.id,
    reason,
    before: { status: "draft", activeVersionId: previous?.id ?? null },
    after: { status: "active", version: activated.version },
  };
  await audit?.append(event);
  return ok(activated);
}

/**
 * Marks the active version `rolled_back` and re-activates the most recent superseded version
 * (highest version number).
 */
export async function rollback<T>(
  store: CalibrationStore<T>,
  input: RollbackInput,
  audit?: AuditSink,
): Promise<Result<CalibrationVersion<T>, CalibrationRefusal>> {
  const reason = input.reason.trim();
  if (!reason) return refuse("REASON_REQUIRED", "a rollback needs a reason");
  if (!input.actor.trim()) return refuse("ACTOR_REQUIRED", "a rollback needs an actor");
  const versions = await store.listVersions();
  const current = versions.find((version) => version.status === "active");
  if (!current) return refuse("NO_ACTIVE", "there is no active version to roll back");
  const previous = versions
    .filter((version) => version.status === "superseded")
    .sort((a, b) => b.version - a.version)[0];
  if (!previous) return refuse("NO_PREVIOUS", "there is no superseded version to restore");
  const at = input.at ?? new Date().toISOString();
  await store.saveVersion({ ...current, status: "rolled_back" });
  const restored: CalibrationVersion<T> = {
    ...previous,
    status: "active",
    activatedAt: at,
    activatedBy: input.actor,
  };
  await store.saveVersion(restored);
  await audit?.append({
    at,
    actor: input.actor,
    action: "calibration.rollback",
    target: restored.id,
    reason,
    before: { activeVersionId: current.id, version: current.version },
    after: { activeVersionId: restored.id, version: restored.version },
  });
  return ok(restored);
}
