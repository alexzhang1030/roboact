"""Acceptance 19.8 and 19.10: pre-push private-IP block, and the dev toolchain."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import ROOT, missing_browser

CHECK = ROOT / "scripts" / "check.sh"


def _code_lines(path: Path) -> str:
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0]
        if code.strip():
            kept.append(code)
    return "\n".join(kept)


def _private_addr() -> str:
    # Built at runtime so the tracked test source has no private address.
    return "192" + ".168" + ".1" + ".20"


def test_19_8_pre_push_blocks_private_addresses(tmp_path: Path) -> None:
    """A tracked 192.168 address in .agents/docs or docs blocks the pre-push check."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".agents" / "docs").mkdir(parents=True)
    (repo / "docs").mkdir()
    for name in ("check_private_ip.py", "pre-push.sh"):
        (repo / "scripts" / name).write_text(
            (ROOT / "scripts" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    mode = (repo / "scripts" / "pre-push.sh").stat().st_mode
    (repo / "scripts" / "pre-push.sh").chmod(mode | stat.S_IEXEC)
    stub = tmp_path / "bin"
    stub.mkdir()
    gitleaks = stub / "gitleaks"
    gitleaks.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    gitleaks.chmod(gitleaks.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": str(stub) + os.pathsep + os.environ.get("PATH", "")}
    subprocess.run(["git", "init"], cwd=repo, check=True, env=env)
    addr = _private_addr()
    agents = repo / ".agents" / "docs" / "planted.md"
    docs = repo / "docs" / "planted.md"
    agents.write_text("plant " + addr + "\n", encoding="utf-8")
    docs.write_text("plant " + addr + "\n", encoding="utf-8")
    untracked = subprocess.run(
        ["bash", "scripts/pre-push.sh"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert untracked.returncode == 0, untracked.stderr
    subprocess.run(
        ["git", "add", ".agents/docs/planted.md", "docs/planted.md"],
        cwd=repo,
        check=True,
        env=env,
    )
    blocked = subprocess.run(
        ["bash", "scripts/pre-push.sh"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked.returncode == 1
    out = blocked.stdout + blocked.stderr
    assert ".agents/docs/planted.md" in out
    assert "docs/planted.md" in out
    assert addr in out
    hook = (ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")
    pre = (ROOT / "scripts" / "pre-push.sh").read_text(encoding="utf-8")
    assert "scripts/pre-push.sh" in hook
    assert "scripts/check_private_ip.py" in pre


def test_19_10_1_check_sh_order() -> None:
    """scripts/check.sh runs the gate in the acceptance order."""
    body = _code_lines(CHECK)
    markers = [
        "uv sync --locked",
        '"${RUN[@]}" check',
        "format --check",
        '"${PYRIGHT[@]}"',
        "--project pyrightconfig.tests.json",
        '"${PYTEST[@]}"',
        "scripts/check_private_ip.py",
        "gitleaks detect",
    ]
    indexes = [body.index(m) for m in markers]
    assert indexes == sorted(indexes)


def test_19_10_2_pyright_strict_and_basic_no_file_ignores() -> None:
    """Package pyright is strict, tests are basic, and ignores are not file-wide."""
    pkg = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    tests = (ROOT / "pyrightconfig.tests.json").read_text(encoding="utf-8")
    assert 'include = ["roboact"]' in pkg
    assert 'typeCheckingMode = "strict"' in pkg
    assert '"include": ["tests"]' in tests
    assert '"typeCheckingMode": "basic"' in tests
    for folder in ("roboact", "tests"):
        for path in (ROOT / folder).rglob("*.py"):
            type_mark = "# type" + ": ignore"
            py_mark = "# pyr" + "ight:"
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if type_mark in line or py_mark in line:
                    tail = line.split("#", 1)[1].strip()
                    assert not tail.endswith("ignore"), f"{path}:{i} has no reason"
                    assert "type: ignore" not in tail or len(tail.split()) > 3, (
                        f"{path}:{i} needs a one-line reason"
                    )


def test_19_10_3_ci_runs_check_sh() -> None:
    """GitHub CI runs scripts/check.sh, including basic pyright on tests."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    assert "bash scripts/check.sh" in ci
    assert "pyrightconfig.tests.json" in check
    assert "continue-on-error" not in ci
    assert "|| true" not in check
    arm = ci.split("test-aarch64:", 1)[1]
    assert "ubuntu-24.04-arm" in ci
    assert "uv sync --frozen" in arm
    assert "pytest" in arm
    assert "playwright install" in arm


def test_19_10_6_python_3_12() -> None:
    """The project is Python 3.12."""
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
    pkg = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12"' in pkg
    assert 'pythonVersion = "3.12"' in pkg
    assert 'python-version: "3.12"' in (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert sys.version_info[:2] == (3, 12)


def test_missing_playwright_fails_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing Playwright browser fails the job when CI is set."""
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception):
        missing_browser("chromium missing")
    monkeypatch.delenv("CI")
    with pytest.raises(pytest.skip.Exception):
        missing_browser("chromium missing")
