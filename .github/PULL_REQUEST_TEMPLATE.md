## What and why

<!-- What does this change, and what problem does it solve? Link the issue. -->

## How it was verified

<!-- Tests added or changed. For statistics: the published or hand-computed value the test checks against. -->

## Checklist

- [ ] TypeScript checks pass (`npm run lint`, `typecheck`, `test`, `build`, `example` in `typescript/`)
- [ ] Python checks pass (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest` in `python/`)
- [ ] Behaviour changes are in both languages, with regenerated conformance vectors
- [ ] Every new rate carries its interval and n (no bare percentages)
- [ ] No new runtime dependencies
- [ ] JSDoc / docstrings on new exported symbols
- [ ] `CHANGELOG.md` updated under Unreleased (user-visible changes only)
