"""PRD 19.9 / A40 — Host, Origin, Content-Type checks. No auth."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.conftest import free_port

ROOT = Path(__file__).resolve().parents[1]


def _start(tmp_path):
    port = free_port()
    db = tmp_path / "a40.db"
    proc = subprocess.Popen(
        [sys.executable, "-m", "roboact", "--host", "127.0.0.1", "--port", str(port), "--db", str(db)],
        cwd=str(ROOT),
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "ROBOACT_TEST": "1", "PYTHONPATH": str(ROOT)},
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


def _curl(port: int, extra: list[str], path: str = "/api/bootstrap", method: str = "GET", data: str | None = None) -> tuple[int, bytes]:
    cmd = ["curl", "-sS", "-o", "-", "-w", "%{http_code}", *extra]
    if method != "GET":
        cmd += ["-X", method]
    if data is not None:
        cmd += ["--data", data]
    cmd.append(f"http://127.0.0.1:{port}{path}")
    r = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
    raw = r.stdout
    code = int(raw[-3:].decode())
    body = raw[:-3]
    return code, body


def test_a40_host_origin_content_type(tmp_path):
    proc, port, base = _start(tmp_path)
    try:
        evil_host, evil_body = _curl(port, ["-H", "Host: evil.example"])
        print("A40 Host: evil.example ->", evil_host, evil_body[:200])
        assert evil_host == 403, (evil_host, evil_body)

        plain, plain_body = _curl(
            port,
            ["-H", f"Host: 127.0.0.1:{port}", "-H", "Content-Type: text/plain"],
            path="/api/runs",
            method="POST",
            data='{"flow_id":"x"}',
        )
        print("A40 Content-Type: text/plain ->", plain, plain_body[:200])
        assert plain == 415, (plain, plain_body)

        ws, ws_body = _curl(
            port,
            [
                "-H", f"Host: 127.0.0.1:{port}",
                "-H", "Origin: http://evil.example",
                "-H", "Upgrade: websocket",
                "-H", "Connection: Upgrade",
            ],
        )
        print("A40 WS foreign Origin ->", ws, ws_body[:200])
        assert ws in (400, 403), (ws, ws_body)

        ok, ok_body = _curl(port, ["-H", f"Host: 127.0.0.1:{port}"])
        print("A40 good Host GET bootstrap ->", ok)
        assert ok == 200, (ok, ok_body)
        boot = json.loads(ok_body.decode())
        assert boot.get("flow"), boot
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_non_loopback_host_flag_warns():
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "roboact", "--host", "0.0.0.0", "--port", str(port), "--db", "/tmp/no.db"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        buf = ""
        t0 = time.time()
        while time.time() - t0 < 5:
            line = proc.stdout.readline() if proc.stdout else ""
            buf += line
            if "WARNING" in buf and "no authentication" in buf.lower():
                break
            if proc.poll() is not None:
                rest = proc.stdout.read() if proc.stdout else ""
                buf += rest
                break
        print(buf)
        assert proc.poll() is None, buf
        assert "WARNING" in buf
        assert "no authentication" in buf.lower()
        assert "anyone on that network" in buf.lower()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
