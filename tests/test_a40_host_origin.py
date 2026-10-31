"""PRD 19.9 / A40 — one reject and one allow for Host, Origin, Content-Type."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from tests.conftest import free_port

ROOT = Path(__file__).resolve().parents[1]


def _start(tmp_path: Path) -> tuple[subprocess.Popen[bytes], int]:
    port = free_port()
    db = tmp_path / "a40.db"
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
        env={**os.environ, "ROBOACT_TEST": "1", "PYTHONPATH": str(ROOT)},
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
    return proc, port


def _curl(
    port: int,
    extra: list[str],
    path: str = "/api/bootstrap",
    method: str = "GET",
    data: str | None = None,
) -> tuple[int, bytes]:
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
    return code, raw[:idx]


def _start_body(port: int) -> str:
    url = f"http://127.0.0.1:{port}/api/bootstrap"
    boot = json.loads(urllib.request.urlopen(url, timeout=2).read())
    return json.dumps({"flow_id": boot["flow"]["id"]})


def test_a40_host_reject(tmp_path: Path) -> None:
    proc, port = _start(tmp_path)
    try:
        code, raw = _curl(
            port,
            ["-H", "Host: evil.example"],
            path="/api/runs",
            method="POST",
            data=_start_body(port),
        )
        print("A40 Host reject raw:\n", raw.decode(errors="replace")[:800])
        assert code == 403, (code, raw)
        assert b"bad_host" in raw
        runs = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs", timeout=2).read()
        )
        assert runs.get("runs") == [], runs
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_a40_host_allow(tmp_path: Path) -> None:
    proc, port = _start(tmp_path)
    try:
        code, raw = _curl(port, ["-H", f"Host: 127.0.0.1:{port}"])
        print("A40 Host allow raw:\n", raw.decode(errors="replace")[:400])
        assert code == 200, (code, raw)
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_a40_origin_reject(tmp_path: Path) -> None:
    proc, port = _start(tmp_path)
    try:
        code, raw = _curl(
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
        print("A40 Origin reject raw:\n", raw.decode(errors="replace")[:800])
        assert code == 403, (code, raw)
        assert b"bad_origin" in raw
        runs = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs", timeout=2).read()
        )
        assert runs.get("runs") == [], runs
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_a40_origin_allow(tmp_path: Path) -> None:
    proc, port = _start(tmp_path)
    try:
        code, raw = _curl(
            port,
            [
                "-H",
                f"Host: 127.0.0.1:{port}",
                "-H",
                f"Origin: http://127.0.0.1:{port}",
                "-H",
                "Upgrade: websocket",
                "-H",
                "Connection: Upgrade",
            ],
        )
        print("A40 Origin allow raw:\n", raw.decode(errors="replace")[:800])
        assert code != 403, (code, raw)
        assert b"bad_origin" not in raw
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_a40_content_type_reject(tmp_path: Path) -> None:
    proc, port = _start(tmp_path)
    try:
        code, raw = _curl(
            port,
            ["-H", f"Host: 127.0.0.1:{port}", "-H", "Content-Type: text/plain"],
            path="/api/runs",
            method="POST",
            data=_start_body(port),
        )
        print("A40 Content-Type reject raw:\n", raw.decode(errors="replace")[:800])
        assert code == 415, (code, raw)
        assert b"bad_content_type" in raw
        runs = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs", timeout=2).read()
        )
        assert runs.get("runs") == [], runs
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_a40_content_type_allow(tmp_path: Path) -> None:
    proc, port = _start(tmp_path)
    try:
        code, raw = _curl(
            port,
            ["-H", f"Host: 127.0.0.1:{port}", "-H", "Content-Type: application/json"],
            path="/api/runs",
            method="POST",
            data=_start_body(port),
        )
        print("A40 Content-Type allow raw:\n", raw.decode(errors="replace")[:800])
        assert code == 201, (code, raw)
    finally:
        proc.terminate()
        proc.wait(timeout=3)
