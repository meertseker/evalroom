/**
 * In-memory implementation of {@link EvalStore} and {@link CalibrationStore}. Records are
 * deep-copied on the way in and out, so callers cannot mutate stored state by accident.
 *
 * @module
 */

import type { CalibrationVersion } from "../calibration";
import type { EvalRun } from "../evalrun";
import type { GoldenCase } from "../golden";
import type { Item, Review } from "../review";
import {
  type CalibrationStore,
  DuplicateIdError,
  type EvalStore,
  matchesReview,
  matchesTenant,
  type ReviewFilter,
  type TenantFilter,
} from "./types";

function insert<V extends { id: string }>(map: Map<string, V>, kind: string, value: V): void {
  if (map.has(value.id)) throw new DuplicateIdError(kind, value.id);
  map.set(value.id, structuredClone(value));
}

function copy<V>(value: V | undefined): V | undefined {
  return value === undefined ? undefined : structuredClone(value);
}

/** A store that lives in process memory. Good for tests, notebooks and short scripts. */
export class MemoryStore<TConfig = unknown> implements EvalStore, CalibrationStore<TConfig> {
  readonly #items = new Map<string, Item>();
  readonly #reviews = new Map<string, Review>();
  readonly #golden = new Map<string, GoldenCase>();
  readonly #runs = new Map<string, EvalRun>();
  readonly #versions = new Map<string, CalibrationVersion<TConfig>>();

  /** @inheritdoc */
  async addItem(item: Item): Promise<void> {
    insert(this.#items, "item", item);
  }

  /** @inheritdoc */
  async getItem(id: string): Promise<Item | undefined> {
    return copy(this.#items.get(id));
  }

  /** @inheritdoc */
  async listItems(filter?: TenantFilter): Promise<Item[]> {
    return [...this.#items.values()]
      .filter((item) => matchesTenant(item, filter))
      .map((item) => structuredClone(item));
  }

  /** @inheritdoc */
  async addReview(review: Review): Promise<void> {
    insert(this.#reviews, "review", review);
  }

  /** @inheritdoc */
  async listReviews(filter?: ReviewFilter): Promise<Review[]> {
    return [...this.#reviews.values()]
      .filter((review) => matchesReview(review, filter))
      .map((review) => structuredClone(review));
  }

  /** @inheritdoc */
  async addGolden(golden: GoldenCase): Promise<void> {
    insert(this.#golden, "golden case", golden);
  }

  /** @inheritdoc */
  async getGolden(id: string): Promise<GoldenCase | undefined> {
    return copy(this.#golden.get(id));
  }

  /** @inheritdoc */
  async listGolden(filter?: TenantFilter): Promise<GoldenCase[]> {
    return [...this.#golden.values()]
      .filter((golden) => matchesTenant(golden, filter))
      .map((golden) => structuredClone(golden));
  }

  /** @inheritdoc */
  async addRun(run: EvalRun): Promise<void> {
    insert(this.#runs, "run", run);
  }

  /** @inheritdoc */
  async getRun(id: string): Promise<EvalRun | undefined> {
    return copy(this.#runs.get(id));
  }

  /** @inheritdoc */
  async listRuns(filter?: TenantFilter): Promise<EvalRun[]> {
    return [...this.#runs.values()]
      .filter((run) => matchesTenant(run, filter))
      .map((run) => structuredClone(run));
  }

  /** @inheritdoc */
  async listVersions(): Promise<CalibrationVersion<TConfig>[]> {
    return [...this.#versions.values()]
      .sort((a, b) => a.version - b.version)
      .map((version) => structuredClone(version));
  }

  /** @inheritdoc */
  async getVersion(id: string): Promise<CalibrationVersion<TConfig> | undefined> {
    return copy(this.#versions.get(id));
  }

  /** @inheritdoc */
  async saveVersion(version: CalibrationVersion<TConfig>): Promise<void> {
    this.#versions.set(version.id, structuredClone(version));
  }
}
