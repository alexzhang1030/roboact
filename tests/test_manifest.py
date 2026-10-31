"""PRD 1.1 依赖可追溯。"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_matches():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check_manifest.py")], cwd=ROOT)
    assert r.returncode == 0


def test_tamper_fails(tmp_path):
    target = ROOT / "web/vendor/vue-flow-core.css"
    raw = target.read_bytes()
    try:
        target.write_bytes(raw + b"\n")
        r = subprocess.run([sys.executable, str(ROOT / "scripts/check_manifest.py")], cwd=ROOT)
        assert r.returncode != 0
    finally:
        target.write_bytes(raw)


def test_each_entry_has_required_fields():
    import json

    data = json.loads((ROOT / "vendor-manifest.json").read_text())
    for e in data["files"]:
        assert e["package"]
        assert e["version"]
        assert e["source_url"]
        assert e["conversion"]
        assert e["license"]
        assert e["license_file"]
        assert e["license_file"].startswith("vendor/licenses/")
        assert (ROOT / e["license_file"]).is_file()
        assert "https://local/" not in e["source_url"]
        assert len(e["sha256"]) == 64
        h = hashlib.sha256((ROOT / e["file"]).read_bytes()).hexdigest()
        assert h == e["sha256"]


def test_missing_license_fails():
    import json

    from scripts.check_manifest import collect_errors

    data = json.loads((ROOT / "vendor-manifest.json").read_text())
    data["files"][0] = {k: v for k, v in data["files"][0].items() if k != "license"}
    errs = collect_errors(data, root=ROOT)
    assert any("missing license" in e for e in errs)
