# Releasing

The repository publishes two packages, each from its own tag. Nothing is published from `main` or by hand.

| Package | Registry | Directory | Tag | Workflow |
| --- | --- | --- | --- | --- |
| `evalroom` (Python) | PyPI | `python/` | `python-vX.Y.Z` | `.github/workflows/release-python.yml` |
| `evalroom` (TypeScript) | npm | `typescript/` | `typescript-vX.Y.Z` | `.github/workflows/release-typescript.yml` |

Both workflows refuse to publish when the tag does not match the package version.

## One-time setup

### PyPI (trusted publishing, no token)

1. Sign in at <https://pypi.org> as the account that will own `evalroom` (enable two-factor authentication if it is not on).
2. Open **Your account → Publishing → Add a new pending publisher** (<https://pypi.org/manage/account/publishing/>) and choose **GitHub**:
   - **PyPI project name:** `evalroom`
   - **Owner:** `meertseker`
   - **Repository name:** `evalroom`
   - **Workflow name:** `release-python.yml`
   - **Environment name:** `pypi`
3. In the GitHub repository, open **Settings → Environments → New environment**, name it `pypi`. Optionally add yourself as a required reviewer, so every PyPI upload waits for a click, and restrict it to tags matching `python-v*`.

The pending publisher turns into a normal trusted publisher on the first successful upload. No API token is stored anywhere; the workflow's `id-token: write` permission is what authenticates it.

### npm

1. Sign in at <https://www.npmjs.com> as the account that will own `evalroom`.
2. Create a granular access token (**Access Tokens → Generate New Token → Granular**) with read and write access to the `evalroom` package (for the first publish, to "all packages", then narrow it), and an expiry date.
3. In the GitHub repository, open **Settings → Secrets and variables → Actions → New repository secret**, name it `NPM_TOKEN` and paste the token.

The workflow publishes with `--provenance`, which needs the `id-token: write` permission it already has. When npm trusted publishing is set up for the package, the token can be removed.

## Each release

### Python

1. Update `version` in `python/pyproject.toml`, `__version__` in `python/src/evalroom/__init__.py` and `CLI_VERSION` in `python/src/evalroom/cli/main.py`.
2. Move the `Unreleased` notes in `CHANGELOG.md` under a dated heading for the Python package.
3. Run the checks from `python/`: `uv sync --all-extras && uv run ruff check && uv run ruff format --check && uv run mypy --strict src tests && uv run pytest -q && uv build`.
4. Commit, then tag and push the tag:

   ```sh
   git tag python-v0.1.0
   git push origin python-v0.1.0
   ```

5. The workflow builds with `uv build`, checks the tag against `pyproject.toml`, runs the tests and uploads the wheel and sdist to PyPI through the `pypi` environment.

### TypeScript

1. Update `version` in `typescript/package.json`, `VERSION` in `typescript/src/index.ts` and `CLI_VERSION` in `typescript/src/cli/main.ts`.
2. Move the `Unreleased` notes in `CHANGELOG.md` under a dated heading for the TypeScript package.
3. Run the checks from `typescript/`: `npm ci && npm run lint && npm run typecheck && npm test && npm run build && npm run example`.
4. Commit, then tag and push the tag:

   ```sh
   git tag typescript-v0.1.0
   git push origin typescript-v0.1.0
   ```

5. The workflow runs the checks, compares the tag with `package.json` and runs `npm publish --provenance --access public`.

## Keeping the two in step

A behaviour change lands in both implementations in the same pull request, with a conformance vector (`npm run conformance:generate`, then commit the regenerated `conformance/*.json`). Versions may differ between the packages; the CHANGELOG says which package a change belongs to.
