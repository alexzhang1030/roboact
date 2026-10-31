"""Python 3.12 / no rclpy. Source: REP 2000 + ROS apt, not PyPI."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "roboact"


def test_python_version_is_3_12() -> None:
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12"' in text
    assert "rclpy" not in text
    assert "[project.optional-dependencies]" not in text


def test_core_package_never_imports_rclpy() -> None:
    hits: list[str] = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "rclpy" or alias.name.startswith("rclpy."):
                        hits.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "rclpy" or mod.startswith("rclpy."):
                    hits.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert hits == [], hits


def test_ci_order_and_aarch64_job() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'python-version: "3.12"' in text
    assert "uv sync --locked" in text
    assert "uv run ruff check" in text
    assert "uv run ruff format --check" in text
    assert "uv run pyright" in text
    assert "uv run pytest" in text
    assert "check_private_ip.py" in text
    assert "gitleaks" in text
    assert "ubuntu-24.04-arm" in text
    assert "uv sync --frozen" in text
    locked_at = text.index("uv sync --locked")
    ruff_at = text.index("uv run ruff check")
    format_at = text.index("uv run ruff format --check")
    pyright_at = text.index("uv run pyright")
    pytest_at = text.index("uv run pytest -q --tb=short")
    ip_at = text.index("check_private_ip.py")
    leaks_at = text.index("gitleaks")
    assert locked_at < ruff_at < format_at < pyright_at < pytest_at < ip_at < leaks_at


def test_check_sh_matches_ci_order() -> None:
    text = (ROOT / "scripts/check.sh").read_text(encoding="utf-8")
    locked_at = text.index("uv sync --locked")
    ruff_at = text.index('"${RUN[@]}" check')
    format_at = text.index("format --check")
    pyright_at = text.index('"${PYRIGHT[@]}"')
    pytest_at = text.index('"${PYTEST[@]}"')
    ip_at = text.index("check_private_ip.py")
    leaks_at = text.index("gitleaks detect")
    assert locked_at < ruff_at < format_at < pyright_at < pytest_at < ip_at < leaks_at


def test_pre_commit_pins_ruff() -> None:
    text = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "rev: v0.17.3" in text
    assert "id: ruff" in text
    assert "id: ruff-format" in text
