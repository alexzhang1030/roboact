# Toolchain

**Current conclusion.** Python **3.12** (`requires-python = ">=3.12"`, `.python-version` is `3.12`). The robot controller (Jetson Thor, aarch64) runs ROS 2 Jazzy on Ubuntu 24.04; system Python is 3.12 (REP 2000).

Use **uv** for the venv and lockfile. Runtime dependency stays exactly `pydantic==2.14.2`. Dev group: ruff, pyright, pytest, playwright, pillow. Frontend stays zero-build; no Node toolchain.

**rclpy is not a dependency.** It comes from apt with ROS, not PyPI. Do not declare it in `pyproject.toml`, not even in an optional group. The core package must never import it. When a ROS adapter lands later, import rclpy only in that adapter module, with a line-level `# pyright: ignore[reportMissingImports]` (never a project-wide switch).

On the controller, create the venv from the ROS system Python:

```
uv venv --python /usr/bin/python3 --system-site-packages
uv sync --frozen
```

Local check: `scripts/check.sh` (ruff check, ruff format --check, pyright, pytest, private-IP scan over `git ls-files`, gitleaks). CI uses `astral-sh/setup-uv`, `uv sync --locked`, then the **same script**. A second job on `ubuntu-24.04-arm` runs `uv sync --frozen` and pytest so a missing aarch64 wheel fails now. `uv.lock` must resolve for aarch64.

Ruff: lint E, F, W, I, B, UP, SIM, RUF; `ruff format`; line length 100. Pyright: strict for `roboact`, basic for `tests`. pre-commit runs ruff check + ruff format (pinned rev).

## Why

Match the controller’s system Python. Keep FakeAdapter-only v1 free of ROS imports so a laptop without ROS can type-check and test. Lock both CPU architectures before a real robot.

## Rejected

- Adding rclpy (or an extras group that pulls it) from PyPI.
- A project-wide pyright switch for missing ROS imports.
- Node / bundler tooling for the canvas.
- `uv run poe check` or another task-runner wrapper. The entry is `scripts/check.sh`.

## Who / date / spec

Alex, 2026-10-31. [PRD 19.10](../../docs/prd.md), [README](../../README.md).
