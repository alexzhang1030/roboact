"""PRD 19.9 / A40 — Host, Origin, Content-Type checks. No auth. Loopback bind only."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from roboact.server import require_loopback_host

from tests.conftest import free_port

ROOT = Path(__file__).resolve().parents[1]


def _start(tmp_path: Path) -> tuple[subprocess.Popen[bytes], int, str]:
    port = free_port()
    db = tmp_path / "a40.db"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "roboact",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--db",
            str(db),
        ],
        cwd=str(ROOT),
        env={
            **dict(**{k: v for k, v in __import__("os").environ.items()}),
            "ROBOACT_TEST": "1",
            "PYTHONPATH": str(ROOT),
        },
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
    return proc, port, base


def _curl(
    port: int,
    extra: list[str],
    path: str = "/api/bootstrap",
    method: str = "GET",
    data: str | None = None,
) -> tuple[int, bytes, str]:
    cmd = ["curl", "-sS", "-D", "-", "-o", "-", "-w", "\nHTTP_CODE:%{http_code}\n", *extra]
    if method != "GET":
        cmd += ["-X", method]
    if data is not None:
        cmd += ["--data", data]
    cmd.append(f"http://127.0.0.1:{port}{path}")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=8)
    raw = r.stdout
    marker = b"\nHTTP_CODE:"
    idx = raw.rfind(marker)
    if idx < 0:
        raise RuntimeError(r.stderr.decode() or raw.decode(errors="replace"))
    code = int(raw[idx + len(marker) :].strip())
    body = raw[:idx]
    return code, body, r.stderr.decode()


def test_a40_host_origin_content_type(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    proc, port, _base = _start(tmp_path)
    try:
        boot = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/bootstrap", timeout=2).read()
        )
        flow_id = boot["flow"]["id"]
        start_body = json.dumps({"flow_id": flow_id})

        evil_host, evil_raw, _ = _curl(
            port,
            ["-H", "Host: evil.example"],
            path="/api/runs",
            method="POST",
            data=start_body,
        )
        print("A40 Host: evil.example raw:\n", evil_raw.decode(errors="replace")[:800])
        assert evil_host == 403, (evil_host, evil_raw)

        plain, plain_raw, _ = _curl(
            port,
            ["-H", f"Host: 127.0.0.1:{port}", "-H", "Content-Type: text/plain"],
            path="/api/runs",
            method="POST",
            data=start_body,
        )
        print("A40 Content-Type: text/plain raw:\n", plain_raw.decode(errors="replace")[:800])
        assert plain == 415, (plain, plain_raw)

        ws, ws_raw, _ = _curl(
            port,
            [
                "-H",
                f"Host: 127.0.0.1:{port}",
                "-H",
                "Origin: http://evil.example",
                "-H",
                "Upgrade: websocket",
                "-H",
                "Connection: Upgrade",
            ],
        )
        print("A40 WS foreign Origin raw:\n", ws_raw.decode(errors="replace")[:800])
        assert ws in (400, 403), (ws, ws_raw)

        ok, ok_raw, _ = _curl(port, ["-H", f"Host: 127.0.0.1:{port}"])
        assert ok == 200, (ok, ok_raw)

        runs = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs", timeout=2).read()
        )
        print("A40 runs after rejected commands:", runs)
        assert runs.get("runs") == [], runs
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        captured = capsys.readouterr()
        print(captured.out)


def test_non_loopback_host_flag_exits() -> None:
    require_loopback_host("127.0.0.1")
    require_loopback_host("localhost")
    require_loopback_host("::1")
    require_loopback_host("[::1]")
    with pytest.raises(SystemExit) as ei:
        require_loopback_host("0.0.0.0")
    assert ei.value.code == 2

    port = free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "roboact",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--db",
            "/tmp/no.db",
        ],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        out, _ = proc.communicate(timeout=5)
        print(out)
        assert proc.returncode == 2, out
        assert "not loopback" in out
        assert "ssh -L 17465:127.0.0.1:17465" in out
        assert "WARNING" not in out
    finally:
        if proc.poll() is None:
            proc.kill()
