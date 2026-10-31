# Toolchain

**Current conclusion.** Python **3.12** (`requires-python = ">=3.12"`, `.python-version` is `3.12`). Source: the robot controller (Jetson Thor, aarch64) runs ROS 2 Jazzy on Ubuntu 24.04; system Python is 3.12 per [REP 2000](https://www.ros.org/reps/rep-2000.html). CI tests on 3.12.

Use **uv** for the venv and lockfile. Runtime dependency stays exactly `pydantic==2.14.2`. Dev group: ruff, pyright, pytest, playwright, pillow. Frontend stays zero-build; no Node toolchain.

**rclpy is not a dependency.** It comes from apt with ROS, not PyPI. Do not declare it in `pyproject.toml`, not even in an optional group. The core package must never import it. When a ROS adapter lands later, import rclpy only in that adapter module, with a line-level `# pyright: ignore[reportMissingImports]` (never a project-wide switch).

On the controller, create the venv from the ROS system Python:

```
uv venv --python /usr/bin/python3 --system-site-packages
uv sync --frozen
```

Local check: `scripts/check.sh` (same order as CI: ruff check, ruff format --check, pyright, pytest, private-IP scan over `git ls-files`, gitleaks). CI on x86_64: `uv sync --locked`, then that order (Python 3.12). A second job on `ubuntu-24.04-arm` runs `uv sync --frozen` and pytest so a missing aarch64 wheel fails now. `uv.lock` must resolve for aarch64.

Ruff: lint E, F, W, I, B, UP, SIM, RUF; `ruff format`; line length 100. Pyright: strict for `roboact`, basic for `tests`. pre-commit runs ruff check + ruff format (pinned rev).

## Why

Match the controller’s system Python. Keep FakeAdapter-only v1 free of ROS imports so a laptop without ROS can type-check and test. Lock both CPU architectures before a real robot.

## Rejected

- Adding rclpy (or an extras group that pulls it) from PyPI. Official PRD v1.4.11 19.10 first wrote an adapter optional extra; Alex 2026-10-31: not declared anywhere — ROS apt + `--system-site-packages` only.
- A project-wide pyright switch for missing ROS imports.
- Node / bundler tooling for the canvas.
- `uv run poe check` or another task-runner wrapper. The entry is `scripts/check.sh`.

## Who / date / spec

Alex, 2026-10-31. Official [PRD v1.4.11 §5 / 19.8 / 19.10](../../docs/prd.md) (31g). rclpy override as above. [README](../../README.md).
