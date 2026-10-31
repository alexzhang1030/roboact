# Public-repo hygiene

**Decided:** History is permanent. Never commit raw chat logs, robot or controller IP addresses or hostnames, non-public vendor docs, or tokens. `127.0.0.1` and `0.0.0.0` may appear; other private IPv4 (10/8, 192.168/16, 172.16–31/12) must not.

A pre-push script and a CI job scan exactly `git ls-files` (including `docs/`) and fail on a match. Run that scan together with gitleaks before a GitHub push.

**Why:** The repository is public. A leaked controller address or token cannot be un-published from git history.

**Who:** Alex

**Date:** 2026-10-31

**Spec:** [PRD 19.8 intent](../prd.md), `scripts/check_private_ip.py`, `.githooks/pre-push`
