# ADR 0002: No bare rates

- Status: accepted
- Date: 2026-10-25

## Context

In the production system, the most expensive mistakes were not bugs; they were decisions made on small numbers. "83% on the new prompt versus 75% on the old one" was 10/12 against 9/12. "Reviewers agree 90% of the time" was nine items. A percentage without its n invites that reading, and dashboards strip the n first.

## Decision

No public function returns a bare proportion. Every rate is an `Interval`:

```ts
interface Interval {
  p: number;
  low: number;
  high: number;
  k: number;
  n: number;
  decidable: boolean;
}
```

computed with the Wilson score interval. Text output goes through `formatInterval`, which always prints rate, counts and bounds together, and appends `not decidable` when `n < minN` (default 50) or the interval is wider than `maxWidth` (default 0.25). Kappa values carry their n, and are `null` with a `reason` below 10 items. `gate` returns `undecidable` instead of guessing.

## Consequences

- Reports are longer: `58% (11/19) · 36%–77% · not decidable` instead of `58%`. That is the point.
- Callers can still read `interval.p`; nothing prevents misuse, but the default path is honest.
- Small teams will see `undecidable` a lot at first. The answer is more reviewed cases, or deliberately lowered thresholds in reviewed code, not a different statistic.
- Kappa has no interval yet; it is marked provisional below 50 items until bootstrap intervals land.
