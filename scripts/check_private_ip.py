#!/usr/bin/env python3
"""Fail if any tracked file contains a private IPv4 address.

Scans exactly `git ls-files` (the set that gets pushed). Dot-directories such
as `.agents/` are included because they are tracked. 127.0.0.1 and 0.0.0.0
are allowed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# RFC1918: 10/8, 172.16–31/12, 192.168/16
PRIVATE = re.compile(
    r"(?<![\d.])("
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
    r")(?![\d.])"
)


def tracked_files() -> list[str]:
    r = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [p.decode() for p in r.stdout.split(b"\0") if p]


def main() -> int:
    hits: list[str] = []
    for rel in tracked_files():
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = PRIVATE.search(line)
            if m:
                hits.append(f"{rel}:{i}: {m.group(1)}")
    if hits:
        print("private IPv4 addresses found:")
        for h in hits:
            print(h)
        return 1
    print("ok: no private IPv4 in git ls-files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
