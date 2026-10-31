# Toolchain

Python 3.12. `rclpy` is not a package dependency.

```bash
uv sync --frozen
uv run python -m roboact
scripts/check.sh
```

`scripts/check.sh`: uv lock, ruff, format, pyright (app + tests), pytest, private-IP over `git ls-files`, gitleaks.
