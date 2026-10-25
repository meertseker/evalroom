/**
 * Append-only audit trail. Golden promotion and calibration draft, activation and rollback
 * each write one {@link AuditEvent}.
 *
 * @module
 */

import { appendFile, mkdir, readFile } from "node:fs/promises";
import { dirname } from "node:path";

/** One immutable audit record. */
export interface AuditEvent {
  /** ISO-8601 timestamp. */
  at: string;
  /** Who did it. */
  actor: string;
  /** What was done, e.g. `"golden.promote"`, `"calibration.activate"`. */
  action: string;
  /** Id of what it was done to. */
  target: string;
  /** Why, when the caller gave a reason. */
  reason?: string;
  /** State before, when meaningful. */
  before?: unknown;
  /** State after, when meaningful. */
  after?: unknown;
  /** Optional tenant tag. */
  tenant?: string;
}

/** Filter for {@link AuditSink.list}. */
export interface AuditFilter {
  /** Only events with this action. */
  action?: string;
  /** Only events with this target. */
  target?: string;
  /** Only events by this actor. */
  actor?: string;
  /** Only events with this tenant. */
  tenant?: string;
}

/** Where audit events go. Implementations must be append-only. */
export interface AuditSink {
  /** Appends one event. */
  append(event: AuditEvent): Promise<void>;
  /** Lists events in append order, optionally filtered. */
  list(filter?: AuditFilter): Promise<AuditEvent[]>;
}

function matches(event: AuditEvent, filter: AuditFilter): boolean {
  return (
    (filter.action === undefined || event.action === filter.action) &&
    (filter.target === undefined || event.target === filter.target) &&
    (filter.actor === undefined || event.actor === filter.actor) &&
    (filter.tenant === undefined || event.tenant === filter.tenant)
  );
}

/** In-memory {@link AuditSink}. Events are deep-copied on the way in and out. */
export class MemoryAuditSink implements AuditSink {
  readonly #events: AuditEvent[] = [];

  /** @inheritdoc */
  async append(event: AuditEvent): Promise<void> {
    this.#events.push(structuredClone(event));
  }

  /** @inheritdoc */
  async list(filter: AuditFilter = {}): Promise<AuditEvent[]> {
    return this.#events
      .filter((event) => matches(event, filter))
      .map((event) => structuredClone(event));
  }
}

/** File-backed {@link AuditSink}: one JSON object per line, appended, never rewritten. */
export class JsonlAuditSink implements AuditSink {
  /** Path of the `.jsonl` file. Created on first append. */
  readonly path: string;

  /** @param path file to append to; parent directories are created as needed. */
  constructor(path: string) {
    this.path = path;
  }

  /** @inheritdoc */
  async append(event: AuditEvent): Promise<void> {
    await mkdir(dirname(this.path), { recursive: true });
    await appendFile(this.path, `${JSON.stringify(event)}\n`, "utf8");
  }

  /** @inheritdoc */
  async list(filter: AuditFilter = {}): Promise<AuditEvent[]> {
    let text: string;
    try {
      text = await readFile(this.path, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
    return text
      .split(/\r?\n/)
      .filter((line) => line.trim() !== "")
      .map((line) => JSON.parse(line) as AuditEvent)
      .filter((event) => matches(event, filter));
  }
}
