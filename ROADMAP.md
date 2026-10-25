# Roadmap

Plans, not promises. Open an issue before starting on any of these so we can agree on the shape.

## Next (0.2)

- **Adjudication.** Record an adjudicated verdict for a split item without touching the lens reviews, and let adjudicated items be promoted to golden.
- **Kappa intervals.** Bootstrap confidence intervals for Fleiss' and Cohen's kappa, so kappa follows the same "no bare numbers" rule as rates.
- **Paired comparison.** McNemar's test (exact for small n) on the broken/fixed partition, to say whether a candidate is better than its base rather than only whether it clears a bar.
- **Browser-safe entry point.** `evalroom/core` without `node:fs`, so the statistics run in a review UI.
- **Judge checks per slice.** `judge-check --by <metadata field>`: agreement per category, with the multiple-comparison caveat, because a judge can be fine overall and bad on one kind of question.
- **Review sampling.** A helper that picks a seeded random sample of an Inspect log for review, so the measured agreement is not biased towards suspicious grades.

## Later

- Krippendorff's alpha, which handles missing lenses and more than one reviewer per lens.
- Same-lens reliability report: several reviewers grading the same lens on the same items.
- Review queue helpers: items missing a lens, oldest first; items to re-review after a rubric bump.
- Stratified gates (per segment or per error class), with the multiple-comparison caveat spelled out.
- Importers for exports from common annotation and observability tools.

## Not planned

- A hosted service, dashboard or database. See [ADR 0001](docs/adr/0001-no-database.md).
- LLM-as-judge scoring. evalroom measures judges, it does not run them: use Inspect (or another tool built for it) and point `judge-check` at the log.
