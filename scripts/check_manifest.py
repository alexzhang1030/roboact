#!/usr/bin/env python3
"""Recompute sha256 of every vendored file and require a SPDX license field."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "vendor-manifest.json"
LICENSE_DIR = "vendor/licenses"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_errors(data: dict, root: Path = ROOT) -> list[str]:
    files = data.get("files") or []
    if not files:
        return ["vendor-manifest.json has no files"]
    errors: list[str] = []
    for entry in files:
        rel = entry.get("file") or "<missing file>"
        lic = entry.get("license")
        if not lic or not str(lic).strip():
            errors.append(f"missing license (SPDX): {rel}")
        lf = entry.get("license_file")
        if not lf or not str(lf).strip():
            errors.append(f"missing license_file: {rel}")
        else:
            if not str(lf).replace("\\", "/").startswith(LICENSE_DIR + "/"):
                errors.append(f"license_file not under {LICENSE_DIR}/: {rel} -> {lf}")
            lp = root / lf
            if not lp.is_file():
                errors.append(f"missing license text: {lf}")
            elif lp.stat().st_size < 20:
                errors.append(f"license text too short: {lf}")
        path = root / rel
        if not path.is_file():
            errors.append(f"missing: {rel}")
            continue
        got = sha256(path)
        want = entry.get("sha256", "")
        if got != want:
            errors.append(f"sha256 mismatch: {rel}\n  manifest: {want}\n  actual:   {got}")
        size = path.stat().st_size
        if entry.get("bytes") not in (None, size):
            errors.append(f"size mismatch: {rel} manifest={entry.get('bytes')} actual={size}")
    for entry in data.get("fonts") or []:
        name = entry.get("file") or "<missing font file>"
        want = entry.get("sha256", "")
        want_bytes = entry.get("bytes")
        found = False
        for folder in (
            root / "roboact" / "static" / "fonts",
            root / "fonts",
            root / "docs" / "design" / "fonts",
        ):
            path = folder / name
            if not path.is_file():
                continue
            found = True
            got = sha256(path)
            if got != want:
                errors.append(
                    f"font sha256 mismatch: {path}\n  manifest: {want}\n  actual:   {got}"
                )
            if want_bytes not in (None, path.stat().st_size):
                errors.append(
                    f"font size mismatch: {path} manifest={want_bytes} actual={path.stat().st_size}"
                )
        if not found:
            errors.append(f"missing shipped font: {name}")
        if entry.get("license") != "OFL-1.1":
            errors.append(f"font license must be OFL-1.1: {name}")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    manifest = Path(args[0]) if args else MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8"))
    errors = collect_errors(data, root=ROOT)
    if errors:
        print("vendor-manifest check FAILED:", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    nfonts = len(data.get("fonts") or [])
    print(
        f"ok: {len(data.get('files') or [])} files"
        + (f" + {nfonts} fonts" if nfonts else "")
        + " match vendor-manifest.json (hashes + licenses)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
