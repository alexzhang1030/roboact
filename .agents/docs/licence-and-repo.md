# Licence and repo

**Current conclusion.** MIT. The copyright line is exactly `Copyright (c) 2026 alexzhang1030`. Mention MIT in the README. Do not invent a second licence.

This tree is not published to PyPI. Vendor files (Vue, Vue Flow, fonts, Python wheels) are recorded in `vendor-manifest.json` with sha256 and a licence file under `vendor/licenses/`.

## Git

- First GitHub push of this project goes to `main` on `https://github.com/alexzhang1030/roboact` (public). If `main` already has a commit, do not force-push.
- After that first push, every further change is a branch + pull request. Do not merge. Do not force-push.
- Run gitleaks over all commits immediately before a GitHub push.
- Private IPv4 addresses must not appear in `git ls-files`. The scanner is `scripts/check_private_ip.py` (CI runs it). Proof that it works: a tracked `192.168.1.10` under `.agents/docs/` must fail; removing it must pass. `127.0.0.1` and `0.0.0.0` are allowed.

PCR records live in `.agents/docs/`. Do not add `docs/decisions/`.
