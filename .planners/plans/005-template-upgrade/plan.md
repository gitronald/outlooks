---
id: 5
slug: template-upgrade
status: active
branch: feature/template-upgrade
created: 2026-10-13T10:58:50-07:00
concluded:
pr: https://github.com/gitronald/outlooks/pull/17
---

# Upgrade to the proj-template 0.11.2 standard

## Plan

Bring the repo from proj-template 0.11.1 to the latest stable release, v0.11.2,
with the template's `install-template` upgrade, reading the template from the
`v0.11.2` tag.

Classification: **package**.

What v0.11.2 changes here:

1. `pyproject.toml`: pin the dev dependency `ruff>=0.16,<0.17` (the same minor as
   the ruff-pre-commit rev), add `extend-exclude = ["*.md"]` under `[tool.ruff]`,
   and stamp `[tool.proj-template] version = "0.11.2"`.
2. `.pre-commit-config.yaml`: the ruff-pre-commit rev matches the locked ruff.
3. `.claude/hooks/lint-typecheck.sh`: replace with the v0.11.2 copy (the repo's copy is an older template revision with nothing of its own), in the main checkout after the merge, since `.claude/` is gitignored here. The new copy checks
   from the git toplevel when it declares a `[tool.uv.workspace]`.

Every other template-managed file already matches v0.11.x. These differences
are this repo's own and stay as they are:

- `test.yml` tests Python 3.12 to 3.14, matching `requires-python = ">=3.12"`.
- No `settings.local.json`: the shared permission baseline lives in the global settings file.

Checks: `uv sync --all-groups`, ruff check and format check, pyrefly, pytest,
and `pre-commit run --all-files`, then a PR into `dev`.

## Log

- **2026-10-13T10:58:52-07:00** — Activated.
  - Branch: `feature/template-upgrade`
  - Base: `dev` at `ed3f1d1`
  - Worktree: `.worktrees/template-upgrade`
  - PR: pending
- **2026-10-13T10:59:12-07:00** — PR opened: https://github.com/gitronald/outlooks/pull/17
- **2026-10-13T11:05:59-07:00** — Review gate: /code-review at medium over the batch of five v0.11.2 upgrade PRs reviewed together (same change in each). No findings survived; two cosmetic candidates below the confidence cutoff were conscious no-ops. CI green.
