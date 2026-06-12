# AgentNode Release Runbook

Safe release procedure for `agentnode-sdk`. Goal: keep PyPI, git tags,
`sdk/CHANGELOG.md`, and the website changelog consistent — every release, no
exceptions.

Scope: SDK releases only. No server, DB, or backend action belongs in this
runbook (web changelog deploy is a separate, normal web deploy).

## Golden rules

- No PyPI upload without a green test suite.
- No tag without the matching commit on `origin/main`; the tag is pushed
  **after** the production install smoke (invariant: PyPI == tag == origin/main).
- **Plain `v<version>` git tags only. NEVER `sdk-v*`** — `.github/workflows/publish-sdk.yml`
  fires on `sdk-v*` releases and auto-uploads with the repo token.
- Upload with **explicit artifact paths from a versioned dist dir** — never
  `twine upload dist/*` (stale-artifact lesson).
- Website changelog entries only with **verified** PyPI/tag data — no invented
  dates, no links to versions that don't exist there.
- PyPI/TestPyPI uploads are gate-approved per step; tokens live in `.pypirc`,
  never in command lines or output.

## 1. Preflight

- [ ] `git status` clean, `origin/main` up to date, on `main`
- [ ] Decide the version (semver: new feature surface = minor, fixes = patch)
- [ ] Note the release scope: `git log v<prev>..origin/main --oneline`
- [ ] Full non-API suite green:
      `python -m pytest tests/ -q --ignore=tests/test_provider_matrix.py --ignore=tests/test_e2e_runtime.py`
- [ ] Targeted tests for the touched area (sandbox/providers/CLI/vault) green
- [ ] After vault/keychain tests: no stray `agentnode:*` entries in the real
      OS keychain

## 2. Release prep (one commit)

- [ ] Bump version in **both** carriers: `sdk/pyproject.toml` and
      `sdk/agentnode_sdk/__init__.py` (`--version` reads the latter)
- [ ] Add the `sdk/CHANGELOG.md` entry (house style:
      `## <ver> — <title>` + `Added/Changed/Hardened/BREAKING-Upgrade Notes`)
- [ ] Commit: `Prepare <version> release metadata`

## 3. Build + local verification

- [ ] Build into a **clean versioned dir**: `python -m build --outdir dist-<version>/`
- [ ] `twine check dist-<version>/*` (exactly 2 artifacts: wheel + sdist)
- [ ] Inspect the wheel: METADATA version, both console scripts
      (`agentnode`, `agentnode-sdk`), expected new modules present, pinned
      sandbox digest real (never a placeholder), no secrets
- [ ] Fresh-venv smoke from the **local wheel path** (neutral CWD, isolated
      HOME): `agentnode --version` on both CLI names, plus a scope-relevant
      smoke (e.g. `sandbox doctor --json`, piped `agentnode setup`)

## 4. TestPyPI (gate)

- [ ] Upload **explicit paths**:
      `twine upload --repository testpypi dist-<version>/agentnode_sdk-<version>-py3-none-any.whl dist-<version>/agentnode_sdk-<version>.tar.gz`
- [ ] Hash compare: TestPyPI files == local files (byte-identical)
- [ ] Install smoke from TestPyPI **with** `--extra-index-url https://pypi.org/simple`
      (dependencies live on real PyPI)

## 5. Production PyPI (gate)

- [ ] Pre-check: PyPI has no `<version>` yet
- [ ] Upload the same explicit paths to PyPI
- [ ] Hash compare via the PyPI JSON API == local
- [ ] Index propagation lags 1–3 min: poll with a background
      `pip download --no-deps agentnode-sdk==<version>` loop (curl is unreliable)
- [ ] Fresh-venv install smoke from real PyPI (neutral CWD): version, both CLI
      names, scope smoke
- [ ] Push `main`, then tag: `git tag v<version> && git push origin v<version>`
      (plain tag, no GitHub Release)

## 6. Website changelog (separate web-only block)

- [ ] Add the release entry to `web/src/data/changelog.ts`:
      summary (1–2 citable sentences), 3–5 highlights, `breaking` note if any
- [ ] `date` only from a verifiable source: PyPI upload date
      (`dateSource: "pypi"`) or git tag date (`"tag"`) — never guessed
- [ ] `onPyPI`/`hasTag` flags only when actually true (they generate the links)
- [ ] `cd web && npm run build` green; local spot-check of `/changelog`
      (latest version card, anchor `#v<x>-<y>-<z>`, links)
- [ ] Separate commit + normal web deploy (server pull → build →
      `systemctl restart agentnode-web`); API untouched

## 7. Post-release verification

- [ ] PyPI project page shows `<version>` as latest
- [ ] `pip install agentnode-sdk==<version>` works in a fresh venv
- [ ] `agentnode --version` == `<version>`
- [ ] Live `/changelog` shows the new version with correct date and links
- [ ] `git status` clean locally and on the server; tag, PyPI, and
      `origin/main` reference the same state

## 8. Rollback / failure notes

- PyPI versions are effectively immutable: a deleted version's filename can
  never be reused. A bad release is fixed by the **next patch version**, not
  by replacing files.
- Never rewrite a pushed tag. A wrong tag that was never published can be
  deleted and re-created **before** anything references it; after publication
  it stays.
- TestPyPI failures cost nothing: fix, bump nothing, re-upload is impossible
  for the same filename — when artifacts must change, that's a new patch
  version even on TestPyPI.
- The website changelog is plain git — fix forward or `git revert` + redeploy.

## 9. Release checklist (short form)

- [ ] Version bumped (pyproject.toml + __init__.py)
- [ ] Tests green (full non-API suite + targeted)
- [ ] sdk/CHANGELOG.md updated
- [ ] Build green (dist-<version>/, 2 artifacts)
- [ ] twine check green
- [ ] TestPyPI uploaded + hash-verified + install smoke green
- [ ] PyPI uploaded + hash-verified
- [ ] pip install smoke green (fresh venv, real PyPI)
- [ ] main pushed, plain tag v<version> pushed
- [ ] web/src/data/changelog.ts entry added (verified date/links)
- [ ] Website build green, /changelog verified live
- [ ] Git clean everywhere; PyPI == tag == origin/main
