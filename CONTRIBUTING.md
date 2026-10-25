# Contributing to evalroom

Thanks for helping. evalroom is small on purpose; the bar for new surface area is high and the bar for fixes, tests and documentation is low.

## Before you start

- For anything beyond a small fix, open an issue first and describe the problem, not just the solution.
- Read [docs/design.md](docs/design.md) (scope and decisions) and [docs/statistics.md](docs/statistics.md) (what the numbers mean).
- Hard rules that will not change in a pull request: zero runtime dependencies, no database, and no function that returns a bare rate ([ADR 0002](docs/adr/0002-no-bare-rates.md)).

## Setup

```sh
git clone https://github.com/meertseker/evalroom.git
cd evalroom
nvm use        # Node 22 (Node 20 is also supported)
npm ci
```

## Checks

All of these run in CI and must pass:

```sh
npm run lint        # biome
npm run typecheck   # tsc --noEmit, strict
npm test            # vitest
npm run build       # tsup, ESM + CJS + .d.ts
npm run example     # the support-replies walkthrough
```

`npm run format` applies biome's formatting and safe fixes. If you change the example's raw data, regenerate the derived files with `npm run example:build` and commit them.

## Tests

- Every behaviour change needs a test.
- **Statistics changes need a known-value test**: a published example (cite it in the test) or a hand computation written out in a comment. No snapshot tests for numbers.
- Keep tests fast and free of network access. CLI tests run `main()` in-process; one smoke test spawns the real binary.

## Code style

- TypeScript strict, no `any` in public types, JSDoc on every exported symbol.
- Pure functions over plain data. Refusals that can have several reasons return a `Result` listing all of them.
- Neutral vocabulary: item, output, reviewer, lens, tenant.

## Commits and pull requests

- Conventional commits: `feat(agreement): ...`, `fix(rules): ...`, `docs: ...`, `test: ...`, `chore: ...`.
- One logical change per pull request. Update `CHANGELOG.md` under "Unreleased" for user-visible changes.
- Fill in the pull request template, including how you verified the change.

By contributing you agree that your contributions are licensed under the MIT licence and that you will follow the [Code of Conduct](CODE_OF_CONDUCT.md).
