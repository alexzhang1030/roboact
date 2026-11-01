from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]

_prd19_ci_record: dict[str, Any] | None = None
_c11_heartbeat_gap_s: float | None = None


def record_prd19_ci(recorded: dict[str, Any]) -> None:
    """Keep the canvas-perf numbers for the terminal summary / runner log."""
    global _prd19_ci_record
    _prd19_ci_record = recorded


def prd19_ci_log_line(recorded: dict[str, Any] | None = None) -> str:
    payload = recorded if recorded is not None else _prd19_ci_record
    return f"PRD19 canvas perf (CI, record only): {payload}"


def emit_prd19_ci_past_capture(recorded: dict[str, Any], capfd: Any) -> None:
    """Write the CI record past pytest fd capture (no ``-s``)."""
    record_prd19_ci(recorded)
    line = prd19_ci_log_line(recorded)
    notice = f"::notice title=PRD19 canvas perf::{line}"
    with capfd.disabled():
        sys.stderr.write(line + "\n")
        sys.stderr.write(notice + "\n")
        sys.stderr.flush()


def record_c11_heartbeat_gap(gap_s: float) -> None:
    """Keep the 20.13 import-vs-heartbeat number for the CI log."""
    global _c11_heartbeat_gap_s
    _c11_heartbeat_gap_s = gap_s


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:
    del exitstatus, config
    if _prd19_ci_record is not None:
        line = prd19_ci_log_line()
        notice = f"::notice title=PRD19 canvas perf::{line}"
        terminalreporter.write_line(line)
        terminalreporter.write_line(notice)
    if _c11_heartbeat_gap_s is not None:
        line = f"C11_IMPORT_MAX_HEARTBEAT_GAP_S={_c11_heartbeat_gap_s:.4f}"
        notice = f"::notice title=C11 import heartbeat::{line}"
        terminalreporter.write_line(line)
        terminalreporter.write_line(notice)


def chrome_path() -> str | None:
    for p in (
        "/usr/local/bin/google-chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
    ):
        if Path(p).exists():
            return p
    return None


def launch_chromium(playwright_obj: Any, path: str | None = None) -> Any:
    """Launch Chromium, or skip when no browser is installed.

    The aarch64 CI job is `uv sync --frozen` + pytest (wheel check). It does
    not install Playwright's browser. Missing Chromium is not a wheel miss.
    """
    exe = path or chrome_path()
    kwargs: dict[str, Any] = {"args": ["--no-sandbox"]}
    if exe:
        kwargs["executable_path"] = exe
    try:
        return playwright_obj.chromium.launch(**kwargs)
    except Exception as e:
        pytest.skip("playwright/chromium not available: " + str(e))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(tmp_path):
    port = free_port()
    db = tmp_path / "ui.db"
    env = {
        **os.environ,
        "ROBOACT_PORT": str(port),
        "ROBOACT_DB": str(db),
        "ROBOACT_TEST": "1",
        "PYTHONPATH": str(ROOT),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "roboact",
            "--port",
            str(port),
            "--db",
            str(db),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    t0 = time.time()
    while time.time() - t0 < 8:
        if proc.poll() is not None:
            out = proc.stdout.read().decode() if proc.stdout else ""
            raise RuntimeError("server exited: " + out)
        try:
            urllib.request.urlopen(base + "/api/bootstrap", timeout=0.3)
            break
        except Exception:
            time.sleep(0.05)
    else:
        proc.kill()
        raise RuntimeError("server did not start")
    info = {"base": base, "port": port, "proc": proc, "db": str(db)}
    yield info
    info["proc"].terminate()
    try:
        info["proc"].wait(timeout=3)
    except subprocess.TimeoutExpired:
        info["proc"].kill()
