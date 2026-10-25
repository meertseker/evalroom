# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - Unreleased

### Added

- Statistics: `wilson` (with `decidable` gating on `minN` and `maxWidth`), `formatRate`, `formatInterval`, `median`, `fleissKappa`, `cohenKappa`, `interpretKappa`.
- Rubrics: `defineRubric` and `parseRubric` with validation of lenses, error-class codes and lens references.
- Reviews: `validateReview`, `parseItem`, `latestPerLens`, `missingLenses`, `completeItems`, `incompleteItems`, `normalizeSeconds`.
- `agreementReport`: raw agreement, Fleiss' kappa, Cohen's kappa and confusion per lens pair, prevalence, median review time, disagreements, Landis & Koch interpretation.
- Rule engine: `scoreOutput`, `evaluateRule`, `parseRules`, `custom`, `getPath`, `normalizeText`, `containsPhrase`, `deepEqual`.
- Golden cases: `promoteToGolden` with every refusal reason, `promoteAndRecord`, `promotionAuditEvent`.
- Eval runs: `scoreRun`, `summarize`, `compareRuns`, `gate`.
- Calibration: `snapshot`, `diffValues`, `createDraft`, `activate`, `rollback`, `activeVersion`.
- Audit: `AuditEvent`, `MemoryAuditSink`, `JsonlAuditSink`.
- Stores: `EvalStore`, `CalibrationStore`, `MemoryStore`, `JsonlStore`, JSONL and run-file helpers.
- CLI: `evalroom agreement | gate | compare | golden promote | score`, `--json` everywhere.
- Example: `examples/support-replies` (20 items, 3 lenses, 63 reviews, 11 golden cases, two runs).
