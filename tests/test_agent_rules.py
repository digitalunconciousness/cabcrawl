"""docs/agents/RULES.md: the agent rules shared by GATBOX, cabcrawl and the tech tracker.

GATBOX owns them and this copy is vendored byte-identical, like contract/v1/: it must match its
CHECKSUMS, CLAUDE.md must send a session to it first, and any sibling checkout's copy must be
the same (CONTRACT_SIBLINGS overrides where to look).
"""
from __future__ import annotations

import hashlib
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULES = ROOT / "docs" / "agents"
SIBLINGS = ("gatbox", "Gatbox", "arcadetech-tracker", "ArcadeTech-Tracker", "shop-hub")


def _listed():
    out = {}
    for line in (RULES / "CHECKSUMS").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, _, name = line.partition("  ")
            out[name] = digest
    return out


def _tree(path):
    return {p.relative_to(path).as_posix(): p.read_bytes()
            for p in sorted(path.rglob("*")) if p.is_file()}


def test_the_rules_match_their_checksums():
    files = {n: hashlib.sha256(b).hexdigest() for n, b in _tree(RULES).items() if n != "CHECKSUMS"}
    assert files == _listed(), "docs/agents does not match its CHECKSUMS: copy it again from GATBOX"


def test_claude_md_sends_a_session_to_the_rules_first():
    assert "docs/agents/RULES.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_the_rules_are_the_same_as_in_sibling_checkouts():
    env = os.environ.get("CONTRACT_SIBLINGS")
    parent = ROOT.parent
    repos = ([pathlib.Path(p) for p in env.split()] if env is not None else
             [parent / n for n in SIBLINGS] + [pathlib.Path("/opt/refs") / n for n in SIBLINGS])
    found = [r for r in repos if (r / "docs" / "agents" / "CHECKSUMS").is_file()
             and r.resolve() != ROOT.resolve()]
    if not found:
        pytest.skip("no sibling checkout found")
    mine = _tree(RULES)
    differ = [str(r) for r in found if _tree(r / "docs" / "agents") != mine]
    assert not differ, "docs/agents differs from the copy in: " + ", ".join(differ)
