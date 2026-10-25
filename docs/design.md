# Design

## Goal

A small, dependency-free core for the _human_ side of LLM evaluation: is the new prompt or model actually better, and can we trust the people grading it? evalroom computes agreement, versions rubrics, promotes reviewed outputs to a golden set, and gates regression runs on confidence intervals. It composes with automatic-scoring and tracing tools rather than replacing them.

## Hard rules

- TypeScript, strict, ESM + CJS build with types. Node >= 20. Zero runtime dependencies.
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
19. **Commit trailer.** Commits carry the `Co-Authored-By` trailer specified for this build.
