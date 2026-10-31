#!/usr/bin/env bash
# Fail the push if tracked files contain a private IPv4 or a secret.
# Used by .githooks/pre-push and by CI. Scans exactly `git ls-files`.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 scripts/check_private_ip.py
if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks not found on PATH" >&2
  exit 1
fi
gitleaks detect --source . --no-banner --redact --exit-code 1
