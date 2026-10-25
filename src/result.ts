/**
 * A tiny Result type. Functions that can refuse for a domain reason (an incomplete item,
 * an invalid rubric, a self-activation) return a Result instead of throwing, so the caller
 * sees every reason at once.
 *
 * @module
 */

/** Successful outcome carrying a value. */
export interface Ok<T> {
  readonly ok: true;
  readonly value: T;
}

/** Refused outcome carrying the reason(s). */
export interface Err<E> {
  readonly ok: false;
  readonly error: E;
}

/** Either {@link Ok} or {@link Err}. Narrow on `.ok`. */
export type Result<T, E> = Ok<T> | Err<E>;

/** Builds an {@link Ok}. */
export function ok<T>(value: T): Ok<T> {
  return { ok: true, value };
}

/** Builds an {@link Err}. */
export function err<E>(error: E): Err<E> {
  return { ok: false, error };
}

/** `true` for a non-null, non-array object. */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
