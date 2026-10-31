#!/usr/bin/env bash
# Local gate (same order as CI): uv sync --locked, ruff, format, pyright,
# pytest, private-IP over git ls-files, gitleaks.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export ROBOACT_TEST="${ROBOACT_TEST:-1}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required for uv sync --locked" >&2
  exit 1
fi
uv sync --locked

if [[ -x "$ROOT/.venv/bin/ruff" ]]; then
  RUN=("$ROOT/.venv/bin/ruff")
  PYRIGHT=("$ROOT/.venv/bin/pyright")
  PYTEST=("$ROOT/.venv/bin/pytest")
elif command -v uv >/dev/null 2>&1; then
  RUN=(uv run ruff)
  PYRIGHT=(uv run pyright)
  PYTEST=(uv run pytest)
else
  echo "uv or .venv is required" >&2
  exit 1
fi

"${RUN[@]}" check
"${RUN[@]}" format --check
"${PYRIGHT[@]}"
"${PYRIGHT[@]}" --project pyrightconfig.tests.json
"${PYTEST[@]}" -q --tb=short
python3 scripts/check_private_ip.py
if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks not found on PATH" >&2
  exit 1
fi
gitleaks detect --source . --no-banner --redact --exit-code 1
