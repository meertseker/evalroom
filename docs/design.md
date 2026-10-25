# Design

## Goal

A small, dependency-free core for the _human_ side of LLM evaluation: is the new prompt or model actually better, and can we trust the people grading it? evalroom computes agreement, versions rubrics, promotes reviewed outputs to a golden set, and gates regression runs on confidence intervals. It composes with automatic-scoring and tracing tools rather than replacing them.

## Hard rules

- Two implementations with one behaviour: TypeScript (strict, ESM + CJS build with types, Node >= 20) in `typescript/` and Python (3.10+, `mypy --strict`, `py.typed`) in `python/`. Zero runtime dependencies in either core; the Python extras (`inspect`, `pandas`) are optional.
- Same files, same numbers: on-disk formats use the same camelCase JSON in both languages, and `conformance/` holds vectors both test suites must reproduce.
- No database. Pure functions over plain data, plus `EvalStore` / `CalibrationStore` interfaces with in-memory and JSONL implementations.
- Neutral vocabulary only: item, output, reviewer, lens, tenant.
- Every rate the library returns carries its interval and n. No function returns a bare percentage ([ADR 0002](adr/0002-no-bare-rates.md)).

## Module map

| Module           | Responsibility                                                                                    |
| ---------------- | ------------------------------------------------------------------------------------------------- |
| `stats.ts`       | `wilson`, `formatRate`, `formatInterval`, `median`, `fleissKappa`, `cohenKappa`, `interpretKappa` |
| `rubric.ts`      | `defineRubric`, `parseRubric`, verdict and severity types                                         |
| `review.ts`      | `Item`, `Review`, validation, `latestPerLens`, `missingLenses`, `completeItems`                   |
| `agreement.ts`   | `agreementReport`                                                                                 |
| `rules.ts`       | Rule engine: `scoreOutput`, `getPath`, `normalizeText`, `custom`, `parseRules`                    |
| `golden.ts`      | `promoteToGolden`, `promoteAndRecord`                                                             |
| `evalrun.ts`     | `scoreRun`, `summarize`, `compareRuns`, `gate`                                                    |
| `calibration.ts` | `snapshot`, `diffValues`, `createDraft`, `activate`, `rollback`                                   |
| `audit.ts`       | `AuditEvent`, `MemoryAuditSink`, `JsonlAuditSink`                                                 |
| `store/`         | `EvalStore`, `CalibrationStore`, `MemoryStore`, `JsonlStore`, JSONL and run-file helpers          |
| `cli/`           | `evalroom` binary (`node:util` `parseArgs`, ANSI formatting, no dependencies)                     |
| `result.ts`      | `Result<T, E>` for refusals that should list every reason                                         |

## What was left behind from the production version, and why

evalroom was extracted from a control room that ran in production at Revint, grading an LLM sales-decision pipeline through three reviewer lenses. The production code was coupled to Prisma, Postgres, a multi-tenant workspace model, role-based access and a specific product domain. What moved and what did not:

| Production feature                                                                                                  | In evalroom                                                                                    | Why                                                                                              |
| ------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Wilson interval with `decidable` (n >= 50, width <= 0.25), `formatRate`, `median`                                   | Ported unchanged, options added                                                                | The core of "no bare rates"; it worked                                                           |
| Fleiss' kappa across lenses, minimum 10 items, raw agreement, prevalence, pairwise agreement, median review seconds | Ported; Cohen's kappa per pair, confusion matrices, Landis & Koch interpretation added         | Reviewers needed to see _which way_ lenses disagreed, not just a number                          |
| Latest review per lens, complete-item logic                                                                         | Ported, generalised to any lens list                                                           | Append-only reviews with re-checks are normal                                                    |
| Rubric with error classes, include/exclude rules and examples, version stamped on every review                      | Ported as `defineRubric` with validation                                                       | Agreement across rubric versions was meaningless in practice                                     |
| Golden promotion requiring all lenses present                                                                       | Ported, plus unanimous PASS by default and a self-check of the rules                           | Promoting a split item silently encoded one reviewer's opinion as truth                          |
| Broken / fixed / same partition of base vs candidate runs                                                           | Ported                                                                                         | The partition, not the pass-rate delta, is what people acted on                                  |
| Domain scoring (recommended package, wedge, score band, allowed modules, forbidden claims and angles)               | Replaced by a generic rule engine                                                              | Domain-specific; the operators it needed (`eq`, `between`, `subsetOf`, `notContains`) generalise |
| Grounding normalisation for forbidden phrases                                                                       | Ported as `normalizeText` and whole-phrase matching                                            | Case and punctuation variants defeated naive substring checks                                    |
| Calibration versions: draft, activation by a different person, rollback, field-level diff lines                     | Ported as pure functions over `CalibrationStore`; generic paths instead of a field-label table | Four-eyes activation caught real mistakes                                                        |
| Admin audit events on every state change                                                                            | Ported as `AuditEvent` + `AuditSink`                                                           | Needed to answer "who changed what"                                                              |
| Prisma persistence, workspace scoping, transactions                                                                 | Replaced by store interfaces and an optional `tenant` tag                                      | Hard rule: no database                                                                           |
| Roles (viewer / reviewer / admin) and per-user lens assignment                                                      | Dropped                                                                                        | Authorisation belongs to the caller's application                                                |
| Adjudication of split items by an admin                                                                             | Dropped for now                                                                                | Needs an opinionated record type; on the roadmap                                                 |
| Candidate replay through a job queue against a live model                                                           | Dropped                                                                                        | evalroom scores outputs; producing them is the caller's job (`score` command)                    |
| Product readiness gates (pipeline reach, coded traces, reference-case counts)                                       | Reduced to the generic `gate`                                                                  | The other gates were product metrics, not review statistics                                      |
| Approved-claim scrubbing of model output                                                                            | Dropped                                                                                        | A generation-time guard, not a review concern                                                    |
| Localised UI strings                                                                                                | Dropped                                                                                        | English only                                                                                     |

## Decisions made during the build

1. **`Interval` has a `k` field** (successes) in addition to `{ p, low, high, n, decidable }`, so an interval can be formatted without re-passing the counts.
2. **Extra internal files.** `src/result.ts` holds the shared `Result` type. The CLI is split into `cli/main.ts` (a testable `main(argv, io)`) and `cli/index.ts` (process wiring), so tests can run it in-process as well as through a child process.
3. **`GoldenCase` adds `referenceOutput`** (the output the reviewers graded) and optional `title` and `tenant`. Promotion also checks, by default, that the expected rules pass on that output (`REFERENCE_OUTPUT_FAILS`).
4. **`expectedRules` are `PathRule[]` only**, so golden cases stay JSON-serialisable. Custom rules are supplied at scoring time via `scoreRun(..., { extraRules })`.
5. **A missing path fails every operator except `exists`**, including the negated ones (`neq`, `notIn`, `notContains`, `notMatches`). An absent field is more often a broken output than a pass.
6. **`contains` / `notContains` on strings match whole phrases after normalisation** (`"refund"` does not match `"refundable"`). On arrays they test membership, with string elements compared after normalisation.
7. **Gate rule.** `undecidable` below `minN` or above `maxWidth` in both directions; `pass` needs the lower bound to clear the bar; `fail` when the point estimate is below it; `undecidable` when the point estimate clears it but the lower bound does not. `minPassRate` is required in the API; the CLI defaults it to 0.9.
8. **`compare` exits 1 when any case broke**, so it can guard CI. The spec only defined exit codes for `gate`.
9. **Exit codes** 64 (usage) and 65 (bad input) follow `sysexits.h`; 70 for unexpected errors.
10. **`golden promote` needs rules from somewhere**, which the spec's command line did not say. It reads `--rules <file>` or falls back to `item.meta.expectedRules`; otherwise it refuses with `NO_RULES`. Also added: `--by`, `--id`, `--title`, `--allow-split`, `--audit`. It refuses to append a case id already present in `--out`.
11. **Run-file format.** A run file is JSONL with an optional first line `{"kind":"run","id","label","rubricVersion","createdAt"}` followed by one `CaseResult` per line. Without the header, id and label come from the file name. `score` reads outputs as `{ "caseId", "output" }` lines.
12. **Stores and sinks are async** so a database-backed implementation fits the same interface. `add*` is insert-only and throws `DuplicateIdError`; calibration versions are upserted by id, and the JSONL store appends each save so history is kept (last line per id wins). Neither store is transactional: run one writer.
13. **Agreement defaults to `rubric.version`** and reports `excludedReviews`; `--version` / `rubricVersion` selects another version with the same lens list.
14. **Median review seconds use every review of the version that recorded time**, including superseded ones, as in production.
15. **Calibration statuses** are `draft | active | superseded | rolled_back`. Only drafts can be activated; rollback restores the superseded version with the highest version number.
16. **The root entry point exports the JSONL store and sink**, which import `node:fs`. That keeps one import path for v0.1 at the cost of browser bundling; a browser-safe entry is on the roadmap.
17. **`formatRate(0, 0)` prints `"no data · not decidable"`.**
18. **The example's Fleiss' kappa is low (0.06) and was not tuned upwards**: its lenses grade different aspects, which is exactly the caveat documented in [statistics.md](statistics.md).

## Python package and Inspect extension

### Layout

| Path | Contents |
| --- | --- |
| `typescript/` | The npm package (moved here with its history), its tests, the support-replies example scripts and `scripts/conformance*.ts` |
| `python/` | The PyPI package: `src/evalroom/` (core, `cli/`, `store/`, `inspect/`, `pandas.py`), `tests/`, `examples/inspect_judge/`, `uv.lock` |
| `conformance/` | Shared vectors (`*.json`, `cli/cases.json`) and the shared dataset (`data/`) |
| `docs/` | Shared documentation |

| Python module | Responsibility |
| --- | --- |
| `stats`, `rubric`, `review`, `agreement`, `rules`, `golden`, `evalrun`, `calibration`, `audit`, `store` | Ports of the TypeScript modules, snake_case names, same semantics |
| `_serde` | snake_case dataclasses to and from the camelCase file format, in one place |
| `_js` | JavaScript-compatible number and JSON formatting (`Math.round`, `toFixed`, `String(number)`, `JSON.stringify`) |
| `jsonl` | JSONL helpers (kept out of `store/` so the audit sink can use them without an import cycle) |
| `cli/` | argparse CLI with the TypeScript commands plus `judge-check` |
| `inspect/` | `judge_check`, `gate_log`, `compare_logs`, `wilson_accuracy`, the entry-point registry |
| `pandas` | `to_dataframe()` |

### Decisions

20. **Python 3.10+ for the core, 3.11+ for the Inspect extra.** inspect_ai 0.3.285 requires Python 3.11, so the `inspect` extra carries a `python_version >= '3.11'` marker. On 3.10 the core and CLI work and the Inspect commands exit 64 with an install hint; the Inspect tests are skipped there. The extra pins `inspect_ai>=0.3.285`, the version the extension was built and tested against.
21. **Stores and audit sinks are synchronous in Python.** The TypeScript ones are async so a database implementation fits; in Python a synchronous protocol is the idiomatic default and an async store can wrap it.
22. **Result values are `Ok` / `Err` dataclasses** (narrow with `isinstance`) so refusals still list every reason; `define_rubric` raises `RubricError` (a `ValueError`) and gate errors are `ValueError`, where TypeScript throws `RangeError`.
23. **`MISSING` stands for JavaScript's `undefined`** where absence and `null` differ: a rule without a value, an item without an input, a key absent from a diffed object.
24. **Printed numbers follow JavaScript.** Rates, kappa (`toFixed(2)`), interval widths and failure details are formatted by `_js` so both CLIs print identical strings; `evalroom score` writes the same result lines in both.
25. **Regular expressions are compiled by each language's engine.** Patterns in the common subset behave the same; flags `i`, `m`, `s` are honoured in Python, `g`, `y`, `u`, `d`, `v` accepted and ignored. Conformance compares regex syntax-error messages by prefix only, because the engines word them differently.
26. **Text normalisation approximates `\p{Diacritic}`** with Unicode combining marks plus modifier symbols (`Mn`, `Sk`); `\p{L}\p{N}` with the `L*`/`N*` categories. Identical on every conformance vector; exotic characters may differ.
27. **`snapshot()` returns a sorted deep copy, not a frozen one**: Python cannot freeze a dict without changing its type.
28. **Conformance vectors are generated from TypeScript** and reference files in `conformance/data` instead of inlining them, to keep the vectors small. Python matched TypeScript on every vector at the first run; no implementation had to be corrected against the published values.

### Inspect extension

29. **Human truth from `Score.history`.** A score is reviewed when its history holds an entry with provenance. The judge's label is `history[0].value` (the snapshot Inspect takes before the first edit, provenance `None`), the human label is the current value, and the last provenance entry supplies the reviewer and reason. An edit that keeps the value is a confirmation. `"UNCHANGED"` (Inspect's not-set marker, which nulls also read back as) is treated as absent. See [inspect-log-format.md](inspect-log-format.md).
30. **Unedited samples are excluded by default.** `--assume-unedited-agree` counts them as agreements; it is opt-in and printed as a warning, because an unopened sample is not evidence.
31. **A labels file replaces the edits** and defines the reviewed set. `epoch` may be omitted unless the sample has several epochs (then the row is ambiguous and refused). Rows matching no sample are counted and reported.
32. **One decision rule everywhere.** `judge-check` uses the core gate's rule on the agreement interval (`evalrun.decide`): undecidable below `--min-n`, above `--max-width`, or when the point estimate clears `--min-agreement` but the lower bound does not. Exit codes 0/1/2 map to acceptable / not acceptable / undecidable.
33. **Partial credit** (`P`, or a number strictly between 0 and 1) counts as incorrect by default (`--partial fail`), configurable to `pass` or `exclude`, in `judge-check`, `gate`, `compare` and the metric.
34. **`wilson_accuracy` returns `{"wilson_accuracy", "lower", "upper", "decidable", "n"}`.** Inspect flattens dict metrics into the scorer's metric list; a key named `accuracy` collided with the built-in `accuracy()` and was renamed `accuracy2`, so the point estimate has a prefixed key. `decidable` is 1.0 or 0.0 so it renders like the other metric values.
35. **The CLI detects Inspect logs** by the `.eval` extension or by a `.json` file whose head looks like an Inspect log; anything else is read as an evalroom run file. Run files keep the existing behaviour, and Inspect-only flags (`--scorer`, `--partial`, `--threshold`) on a run file are a usage error. `--threshold` is the Inspect-side name of `--min-pass`; both are accepted on logs.
36. **Cases from a log** are sample ids (`id#epoch` when the log has several epochs); failing cases carry the score value as their code and an excerpt of the scorer's explanation as their detail.
37. **The example fixture is generated, deterministic in content and committed** (`make_fixture.py`, seed 2026). Its grades and labels never change between runs; Inspect's own ids and timestamps inside the log do. It is about 240 KiB, kept as Inspect wrote it (events and grading transcripts included) so it stays a faithful example.
