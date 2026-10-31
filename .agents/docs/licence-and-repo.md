# Licence and repo

**Current conclusion.** MIT. The copyright line is exactly `Copyright (c) 2026 alexzhang1030`. Mention MIT in the README. Do not invent a second licence.

The public GitHub repository is `alexzhang1030/roboact`. The product name is Roboact. This tree is not published to PyPI. Vendor files (Vue, Vue Flow, fonts, Python wheels) are recorded in `vendor-manifest.json` with sha256 and a licence file under `vendor/licenses/`.

## Git

- First GitHub push of this project goes to `main`. If `main` already has a commit, do not force-push.
- After that first push, every further change is a branch + pull request. Do not merge. Do not force-push.
- Run gitleaks over all commits immediately before a GitHub push.
- Private IPv4 addresses must not appear in `git ls-files`. The scanner is `scripts/check_private_ip.py`. It scans **exactly** `git ls-files` (not a directory glob) so `.agents/` is included. Proof: staging a tracked RFC1918 canary (`192.168` /16, host `.1.10`) under `.agents/docs/` must fail; removing it must pass. `127.0.0.1` and `0.0.0.0` are allowed. Do not write the canary as a dotted quad in committed docs.

PCR records live in `.agents/docs/`. Do not add `docs/decisions/`.

## Why

The repository is public. A leaked controller address or token cannot be unpublished from git history. `rg` and many globs skip dot-directories; `git ls-files` does not.

## Rejected

- A second licence or a PyPI publish config.
- Scanning a directory glob that silently skips `.agents/`.

## Who / date / spec

Alex, 2026-10-31. [PRD header](../../docs/prd.md), [LICENSE](../../LICENSE), [PRD 19.8](../../docs/prd.md).
