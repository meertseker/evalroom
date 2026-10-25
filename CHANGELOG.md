# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and both packages adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The repository ships two packages with independent versions: **Python** (`evalroom` on PyPI, tags `python-v*`) and **TypeScript** (`evalroom` on npm, tags `typescript-v*`).

## [Unreleased]

### Repository

- The TypeScript package moved to `typescript/`; the Python package lives in `python/`; `docs/` is shared.
- `conformance/`: shared test vectors (statistics, agreement, rules, golden promotion, eval runs, gate, calibration, and the `--json` output and exit code of every CLI command) that both implementations must reproduce. The support-replies example data moved to `conformance/data/`.
- CI runs a TypeScript job (Node 20 and 22) and a Python job (3.10 to 3.13). Separate release workflows per package; PyPI uses trusted publishing.

## Python [0.1.0] - Unreleased

### Added

- Port of the full core with the same semantics, defaults, refusal codes and messages as the TypeScript package: `wilson`, `format_rate`, `format_interval`, `median`, `fleiss_kappa`, `cohen_kappa`, `interpret_kappa`; `define_rubric`, `parse_rubric`; `validate_review`, `parse_item`, `latest_per_lens`, `missing_lenses`, `complete_items`, `incomplete_items`, `normalize_seconds`; `agreement_report`; the rule engine (`score_output`, `evaluate_rule`, `parse_rules`, `custom`, `get_path`, `normalize_text`, `contains_phrase`, `deep_equal`); `promote_to_golden`, `promote_and_record`; `score_run`, `summarize`, `compare_runs`, `gate`; calibration (`snapshot`, `diff_values`, `create_draft`, `activate`, `rollback`); audit sinks; `MemoryStore`, `JsonlStore` and the run-file helpers.
- Frozen dataclasses with full type hints (`py.typed`, `mypy --strict` clean). Files use the same camelCase JSON as the TypeScript package and are interchangeable.
- The `evalroom` CLI (`agreement`, `gate`, `compare`, `golden promote`, `score`) with the same flags, `--json` output and exit codes, built on argparse.
- `evalroom[inspect]`: `judge_check` and `evalroom judge-check` (judge vs human agreement from `Score.history` edits or a JSONL/CSV labels file, with Wilson intervals, Cohen's kappa, a confusion matrix, top disagreements and a decision with the core gate's rule); `gate_log`, `compare_logs`, and `evalroom gate` / `compare` on `.eval` and Inspect `.json` logs; the `wilson_accuracy` metric, registered as `evalroom/wilson_accuracy`.
- `evalroom[pandas]`: `to_dataframe()`.
- Example: `python/examples/inspect_judge` (a real 80-sample Inspect log with a judge wrong on 15% of grades and 60 human edits, plus the deterministic generator).

## TypeScript [0.1.0] - Unreleased

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
- Example: `typescript/examples/support-replies` (20 items, 3 lenses, 63 reviews, 11 golden cases, two runs; data in `conformance/data`).
- `npm run conformance:generate` regenerates the shared conformance vectors.
