#!/usr/bin/env python3
"""Check a contract directory against its CHECKSUMS.

    python scripts/contract_checksums.py                    # check contract/v1
    python scripts/contract_checksums.py --dir path/to/v1    # check another copy

Contract v1 is owned by GATBOX (its docs/contract/v1/, since 2026-10-28; this repository owned
it before) and vendored here byte-identical, as it is into the other hub. Each repository
checks its copy against the same CHECKSUMS in its tests. A mismatch here means this copy was
edited: copy the files again from GATBOX, never regenerate CHECKSUMS in this repository
(GATBOX's docs/contract/checksums.sh does that, there). Check only; no --write.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys


def digests(root: pathlib.Path) -> dict[str, str]:
    """path relative to *root* -> sha256, for every file but CHECKSUMS."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "CHECKSUMS":
            out[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return out


def stored(root: pathlib.Path) -> dict[str, str]:
    """What CHECKSUMS claims. Raises FileNotFoundError when there is none."""
    out = {}
    for line in (root / "CHECKSUMS").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        out[name] = digest
    return out


def compare(root: pathlib.Path) -> list[str]:
    """Human-readable problems; empty means the directory matches its CHECKSUMS."""
    actual, claimed = digests(root), stored(root)
    problems = []
    for name in sorted(set(actual) | set(claimed)):
        if name not in claimed:
            problems.append(f"{name}: present but not in CHECKSUMS")
        elif name not in actual:
            problems.append(f"{name}: in CHECKSUMS but missing from {root}")
        elif actual[name] != claimed[name]:
            problems.append(f"{name}: changed since CHECKSUMS was written")
    return problems


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", default="contract/v1", help="the contract directory")
    args = p.parse_args()

    root = pathlib.Path(args.dir)
    if not root.is_dir():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2

    problems = compare(root)
    if problems:
        print(f"{root} does not match its CHECKSUMS:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"{root}: {len(digests(root))} files, all match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
