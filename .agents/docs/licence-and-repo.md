# Licence and repo

**Current conclusion.** Roboact's own licence is **not decided yet**. Do not add a LICENSE file. Do not claim MIT (or any other licence) for this project. Third-party licence texts stay under `vendor/licenses/` and are recorded per file in `vendor-manifest.json`.

The public GitHub repository is `alexzhang1030/roboact`. The product name is Roboact. This tree is not published to PyPI.

## Git

- First GitHub push of this project goes to `main`. If `main` already has a commit, do not force-push.
- After that first push, every further change is a branch + pull request. Do not merge. Do not force-push.
- Run gitleaks over all commits immediately before a GitHub push.
- Private IPv4 addresses must not appear in `git ls-files`. The scanner is `scripts/check_private_ip.py`. It scans **exactly** `git ls-files` (not a directory glob) so `.agents/` is included. Proof: staging a tracked RFC1918 canary (`192.168` /16, host `.1.10`) under `.agents/docs/` must fail; removing it must pass. `127.0.0.1` and `0.0.0.0` are allowed. Do not write the canary as a dotted quad in committed docs.

PCR records live in `.agents/docs/`. Do not add `docs/decisions/`.

## Why

Licence choice is not on record. The repository is public. A leaked controller address or token cannot be unpublished from git history. `rg` and many globs skip dot-directories; `git ls-files` does not.

## Rejected

- Shipping a LICENSE file or calling Roboact MIT before Alex confirms.
- A PyPI publish config.
- Scanning a directory glob that silently skips `.agents/`.

## Who / date / spec

Alex, 2026-10-31. [PRD header](../../docs/prd.md), [PRD 19.8](../../docs/prd.md).
