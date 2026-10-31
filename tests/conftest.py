from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


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
    import urllib.request

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
