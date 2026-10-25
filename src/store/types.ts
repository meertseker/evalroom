/**
 * Storage interfaces. evalroom has no database: these interfaces are the whole persistence
 * contract, and `MemoryStore` and `JsonlStore` are two small implementations of them. Write
 * your own against Postgres, SQLite or an object store if you need one.
 *
 * @module
 */

import type { CalibrationVersion } from "../calibration";
import type { EvalRun } from "../evalrun";
import type { GoldenCase } from "../golden";
import type { Item, Review } from "../review";

/** Filter for {@link EvalStore.listReviews}. */
export interface ReviewFilter {
  /** Only reviews of this item. */
  itemId?: string;
  /** Only reviews of this lens. */
  lens?: string;
  /** Only reviews stamped with this rubric version. */
  rubricVersion?: string;
  /** Only reviews with this tenant tag. */
  tenant?: string;
}

/** Filter for item, golden and run listings. */
export interface TenantFilter {
  /** Only records with this tenant tag. */
  tenant?: string;
}

/** Thrown when a record with the same id already exists. Stores are append-only. */
export class DuplicateIdError extends Error {
  /** Kind of record, e.g. `"review"`. */
  readonly kind: string;
  /** The duplicated id. */
  readonly id: string;

  constructor(kind: string, id: string) {
    super(`${kind} "${id}" already exists`);
    this.name = "DuplicateIdError";
    this.kind = kind;
    this.id = id;
  }
}

/**
 * Persistence for items, reviews, golden cases and runs. Every `add*` is insert-only and
 * rejects an existing id with {@link DuplicateIdError}: reviews supersede each other by
 * `createdAt`, never by overwriting. Listings return records in insertion order.
 */
export interface EvalStore {
  /** Adds an item. */
  addItem(item: Item): Promise<void>;
  /** The item with this id, if any. */
  getItem(id: string): Promise<Item | undefined>;
  /** All items. */
  listItems(filter?: TenantFilter): Promise<Item[]>;
  /** Adds a review. */
  addReview(review: Review): Promise<void>;
  /** Reviews matching the filter. */
  listReviews(filter?: ReviewFilter): Promise<Review[]>;
  /** Adds a golden case. */
  addGolden(golden: GoldenCase): Promise<void>;
  /** The golden case with this id, if any. */
  getGolden(id: string): Promise<GoldenCase | undefined>;
  /** All golden cases. */
  listGolden(filter?: TenantFilter): Promise<GoldenCase[]>;
  /** Adds an eval run. */
  addRun(run: EvalRun): Promise<void>;
  /** The run with this id, if any. */
  getRun(id: string): Promise<EvalRun | undefined>;
  /** All runs. */
  listRuns(filter?: TenantFilter): Promise<EvalRun[]>;
}

/**
 * Persistence for calibration versions. `saveVersion` upserts by id (status changes are
 * saves of the same id); `listVersions` returns the current state of each version, ordered by
 * `version` ascending. Implementations are not required to be transactional: run one writer.
 */
export interface CalibrationStore<T = unknown> {
  /** Every version, current state, ascending by `version`. */
  listVersions(): Promise<CalibrationVersion<T>[]>;
  /** The version with this id, if any. */
  getVersion(id: string): Promise<CalibrationVersion<T> | undefined>;
  /** Inserts or replaces the version with this id. */
  saveVersion(version: CalibrationVersion<T>): Promise<void>;
}

/** `true` when `record` passes `filter`. */
export function matchesTenant(record: { tenant?: string }, filter: TenantFilter = {}): boolean {
  return filter.tenant === undefined || record.tenant === filter.tenant;
}

/** `true` when `review` passes `filter`. */
export function matchesReview(review: Review, filter: ReviewFilter = {}): boolean {
  return (
    (filter.itemId === undefined || review.itemId === filter.itemId) &&
    (filter.lens === undefined || review.lens === filter.lens) &&
    (filter.rubricVersion === undefined || review.rubricVersion === filter.rubricVersion) &&
    (filter.tenant === undefined || review.tenant === filter.tenant)
  );
}
