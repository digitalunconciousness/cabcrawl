"""The library's read side (contract/library-v1/), vendored from GATBOX, byte-identical.

GATBOX owns it (docs/contract/library-v1/read/) and its archive reproduces every example in its own
tests; this repository's pull job reads the same examples through a stub (test_library_pull.py). So
neither end can drift from the other without a test saying so. This file only checks the copy itself:
that it matches its CHECKSUMS and is the same edition as in any sibling checkout.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.path.join(REPO_ROOT, "contract", "library-v1")
SIBLINGS = ("gatbox", "Gatbox", "arcadetech-tracker", "ArcadeTech-Tracker", "shop-hub")


def _checksums():
    path = os.path.join(REPO_ROOT, "scripts", "contract_checksums.py")
    spec = importlib.util.spec_from_file_location("contract_checksums", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


def test_the_read_side_matches_its_checksums():
    problems = _checksums().compare(pathlib.Path(CONTRACT))
    assert not problems, "contract/library-v1 does not match CHECKSUMS:\n  " + "\n  ".join(problems)


def test_the_read_side_is_the_same_as_in_sibling_checkouts():
    """A copy that matches its own CHECKSUMS can still be an older edition than GATBOX's."""
    here = pathlib.Path(CONTRACT).resolve()
    env = os.environ.get("CONTRACT_SIBLINGS")
    parent = pathlib.Path(REPO_ROOT).parent
    repos = ([pathlib.Path(p) for p in env.split()] if env is not None else
             [parent / n for n in SIBLINGS] + [pathlib.Path("/opt/refs") / n for n in SIBLINGS])
    found = []
    for repo in repos:
        for sub in ("docs/contract/library-v1/read", "contract/library-v1"):
            path = repo / sub
            if (path / "CHECKSUMS").is_file() and path.resolve() != here:
                found.append((str(repo), path))
                break
    if not found:
        pytest.skip("no sibling checkout has the library's read side")
    mine = _tree(here)
    differ = [repo for repo, path in found if _tree(path) != mine]
    assert not differ, "contract/library-v1 differs from the copy in: " + ", ".join(differ)
