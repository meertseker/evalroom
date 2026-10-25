# Contributing to evalroom

Thanks for helping. evalroom is small on purpose; the bar for new surface area is high and the bar for fixes, tests and documentation is low.

## Before you start

- For anything beyond a small fix, open an issue first and describe the problem, not just the solution.
- Read [docs/design.md](docs/design.md) (scope and decisions) and [docs/statistics.md](docs/statistics.md) (what the numbers mean).
- Hard rules that will not change in a pull request: zero runtime dependencies in either core, no database, no function that returns a bare rate ([ADR 0002](docs/adr/0002-no-bare-rates.md)), and the same behaviour in both languages.

## Layout

`typescript/` is the npm package, `python/` the PyPI package, `conformance/` the vectors and data both must agree on, `docs/` is shared.

## Setup and checks

All of these run in CI and must pass.

TypeScript (Node 22; 20 is also supported):

```sh
cd typescript
npm ci
npm run lint        # biome
npm run typecheck   # tsc --noEmit, strict
npm test            # vitest, including the conformance vectors
npm run build       # tsup, ESM + CJS + .d.ts
npm run example     # the support-replies walkthrough
```

Python (with [uv](https://docs.astral.sh/uv/)):

```sh
cd python
uv sync --all-extras
uv run ruff check && uv run ruff format --check
uv run mypy --strict src tests
uv run pytest -q    # including the conformance vectors and the Inspect fixture
uv build
```

`npm run format` and `uv run ruff format` apply formatting. If you change the example's raw data, regenerate the derived files with `npm run example:build`; if you change the Inspect example, rerun `uv run python examples/inspect_judge/make_fixture.py`. Commit the results.

## Changing behaviour in both languages

A behaviour change lands in TypeScript and Python in the same pull request. Add or extend the inputs in `typescript/scripts/conformance.ts`, run `npm run conformance:generate`, commit the regenerated `conformance/*.json`, and make both test suites pass. Hand-written published-value cases (entries with a `published` field in `conformance/stats.json`) are never regenerated.

## Tests

- Every behaviour change needs a test.
- **Statistics changes need a known-value test**: a published example (cite it in the test) or a hand computation written out in a comment. No snapshot tests for numbers.
- Keep tests fast and free of network access (Inspect tests use `mockllm/model`; no API keys anywhere). CLI tests run `main()` in-process; one smoke test per language spawns the real binary.

## Code style

- TypeScript strict, no `any` in public types, JSDoc on every exported symbol.
- Python: full type hints (`mypy --strict`), frozen dataclasses for values, docstrings on every public symbol, snake_case in code and camelCase only in files (via `_serde`).
- Pure functions over plain data. Refusals that can have several reasons return a `Result` listing all of them.
- Neutral vocabulary: item, output, reviewer, lens, tenant.

## Commits and pull requests

- Conventional commits: `feat(agreement): ...`, `fix(rules): ...`, `docs: ...`, `test: ...`, `chore: ...`.
- One logical change per pull request. Update `CHANGELOG.md` under "Unreleased" for user-visible changes.
- Fill in the pull request template, including how you verified the change.

By contributing you agree that your contributions are licensed under the MIT licence and that you will follow the [Code of Conduct](CODE_OF_CONDUCT.md).
