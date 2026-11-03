"""The antislop skills: a pinned copy of someone else's rules for agents (docs/antislop/PROVENANCE.md),
byte-identical in all three repositories and owned by GATBOX. This copy matches its CHECKSUMS, grants
its skills no standing permissions, and equals any sibling checkout's (CONTRACT_SIBLINGS overrides
where to look). DESIGN.md is the owner's direction, which says Synthwave is deliberate and
Ledger and Graphite are not exempt.
"""
import hashlib
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".claude" / "skills"
DOC = ROOT / "docs" / "antislop"
SIBLINGS = ("gatbox", "Gatbox", "arcadetech-tracker", "ArcadeTech-Tracker", "shop-hub")


def _skills():
    return sorted(p for p in SKILLS.glob("antislop*") if p.is_dir())


def _tree(path):
    return {p.relative_to(path).as_posix(): p.read_bytes()
            for p in sorted(path.rglob("*")) if p.is_file()}


def _listed():
    out = {}
    for line in (DOC / "CHECKSUMS").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, _, name = line.partition("  ")
            out[name] = digest
    return out


def test_six_skills_each_named_for_its_folder():
    assert len(_skills()) == 6
    for d in _skills():
        assert f"\nname: {d.name}\n" in (d / "SKILL.md").read_text(encoding="utf-8")


def test_the_copy_matches_its_checksums():
    files = {f"{d.name}/{n}": hashlib.sha256(b).hexdigest()
             for d in _skills() for n, b in _tree(d).items()}
    assert files == _listed(), "the antislop copy does not match docs/antislop/CHECKSUMS"


def test_no_skill_grants_itself_tools():
    """Upstream pre-approves Write, Edit and python while a skill is active. Here none does."""
    for path in SKILLS.glob("antislop*/**/*"):
        if path.is_file() and path.suffix in (".md", ".py"):
            assert "allowed-tools" not in path.read_text(encoding="utf-8"), path


def test_its_licence_and_provenance_are_here():
    assert (DOC / "LICENSE").is_file()
    provenance = (DOC / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "3005235d030b4793146ad9afe8d01045efd90e1d" in provenance
    assert (SKILLS / "antislop" / "VERSION").read_text(encoding="utf-8").strip() == "3.2.24"
    assert "3.2.24" in provenance


def test_design_md_and_the_rules_set_the_direction():
    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    assert "Synthwave is deliberate" in design and "not exempt" in design
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "DESIGN.md" in claude and ".claude/skills" in claude
    rules = (ROOT / "docs" / "agents" / "RULES.md").read_text(encoding="utf-8")
    assert "The mode is **during**" in rules


def test_the_copy_is_the_same_as_in_sibling_checkouts():
    env = os.environ.get("CONTRACT_SIBLINGS")
    repos = ([pathlib.Path(p) for p in env.split()] if env is not None else
             [ROOT.parent / n for n in SIBLINGS] + [pathlib.Path("/opt/refs") / n for n in SIBLINGS])
    found = [r for r in repos if (r / "docs" / "antislop" / "CHECKSUMS").is_file()
             and r.resolve() != ROOT.resolve()]
    if not found:
        pytest.skip("no sibling checkout found")
    mine = ({d.name: _tree(d) for d in _skills()}, _tree(DOC))
    differ = [str(r) for r in found
              if ({d.name: _tree(d) for d in sorted((r / ".claude" / "skills").glob("antislop*"))},
                  _tree(r / "docs" / "antislop")) != mine]
    assert not differ, "the antislop copy differs from the one in: " + ", ".join(differ)
