"""scripts/privacy_check.sh, run against a throwaway git repository.

The repository is public, so the pre-push hook runs this check on everything a push would
publish. Each test builds a small repository in a temporary folder, commits something, and
runs the real script on it with an invented owner pattern. Nothing here reads the owner's
real patterns file: CABCRAWL_HOOK_PATTERNS and HOME point into the temporary folder. The script
is the shared one (the same in GATBOX and the tech tracker); this repository's own paths are in
.githooks/privacy.conf.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "privacy_check.sh")
HOOK = os.path.join(ROOT, ".githooks", "pre-push")
NOREPLY = "240414701+digitalunconciousness@users.noreply.github.com"
# Built in pieces so this file does not itself carry an address the check would refuse.
OUTSIDE_EMAIL = "pat.someone" + "@" + "elsewhere-mail.net"
PATTERN = "ZEBRA[0-9]+FROG"        # invented owner pattern
MATCHING = "ZEBRA42FROG"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                                reason="needs git and bash")


def _git(repo, *args, env=None):
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path):
    """An empty repository with the noreply identity, and an env that commits in UTC."""
    path = tmp_path / "repo"
    path.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    patterns = tmp_path / "patterns.local"
    patterns.write_text("# invented\n" + PATTERN + "\n")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1",
        "CABCRAWL_HOOK_PATTERNS": str(patterns),
        "GIT_AUTHOR_NAME": "digitalunconciousness",
        "GIT_COMMITTER_NAME": "digitalunconciousness",
        "GIT_AUTHOR_EMAIL": NOREPLY,
        "GIT_COMMITTER_EMAIL": NOREPLY,
    }
    _git(path, "init", "-q", "-b", "master", env=env)
    commit(path, env, "README.md", "An invented arcade.\n", "Start")
    return path, env


def commit(path, env, name, text, message, **extra):
    target = path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    _git(path, "add", "-f", name, env=env)
    _git(path, "commit", "-q", "-m", message, env={**env, **extra})


def check(path, env, *args):
    return subprocess.run(["bash", SCRIPT, *args], cwd=path, env=env, capture_output=True,
                          text=True, check=False)


def test_a_clean_commit_passes(repo):
    path, env = repo
    commit(path, env, "app/note.txt", "Neon Raider needs a belt.\n", "Add a note")
    result = check(path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "privacy check: clean" in result.stdout
    assert "(1 pattern(s) loaded)" in result.stdout


@pytest.mark.parametrize("name", [
    "instance/arcade.db", "uploads/maintenance_photos/maintenance_1_abcdef12.jpg",
    "static/maintenance_photos/maintenance_1_abcdef12.jpg", ".env", "backups/x.sql.gz",
    "dump.sql", "docs/manual.pdf", "roms/program.bin", "local.sqlite3", "deploy/site.key",
    ".githooks/patterns.local",
])
def test_a_forbidden_path_fails(repo, name):
    path, env = repo
    commit(path, env, name, "synthetic\n", "Add a file")
    result = check(path, env)
    assert result.returncode == 1
    assert f"  {name}" in result.stdout


def test_the_examples_are_not_forbidden(repo):
    path, env = repo
    commit(path, env, ".env.example", "SECRET_KEY=\n", "Example env")
    commit(path, env, ".githooks/patterns.local.example", "# none\n", "Example patterns")
    assert check(path, env).returncode == 0


def test_a_non_utc_timestamp_fails(repo):
    path, env = repo
    commit(path, env, "a.txt", "x\n", "Local time",
           GIT_AUTHOR_DATE="2026-10-01T09:00:00-0500",
           GIT_COMMITTER_DATE="2026-10-01T09:00:00-0500")
    result = check(path, env)
    assert result.returncode == 1
    assert "non-UTC time" in result.stdout


def test_a_commit_email_outside_the_allow_list_fails(repo):
    path, env = repo
    commit(path, env, "a.txt", "x\n", "Wrong identity", GIT_AUTHOR_EMAIL=OUTSIDE_EMAIL)
    result = check(path, env)
    assert result.returncode == 1
    assert f"email {OUTSIDE_EMAIL}" in result.stdout


def test_an_email_in_the_content_fails_and_reserved_domains_pass(repo):
    path, env = repo
    commit(path, env, "ok.txt", "tech@arcade.example.com and noreply@anthropic.com\n", "Fine")
    assert check(path, env).returncode == 0
    commit(path, env, "bad.txt", f"call {OUTSIDE_EMAIL}\n", "Not fine")
    result = check(path, env)
    assert result.returncode == 1
    assert "bad.txt:1" in result.stdout


def test_an_owner_pattern_match_fails_and_is_never_printed(repo):
    path, env = repo
    commit(path, env, "notes.md", f"line one\nthe venue is {MATCHING}\n", "Notes")
    result = check(path, env)
    assert result.returncode == 1
    assert "pattern #1 in notes.md:2" in result.stdout
    assert MATCHING not in result.stdout + result.stderr
    assert PATTERN not in result.stdout + result.stderr


def test_an_owner_pattern_in_a_commit_message_fails(repo):
    path, env = repo
    commit(path, env, "a.txt", "x\n", f"Fix for {MATCHING}")
    result = check(path, env)
    assert result.returncode == 1
    assert "pattern #1 in a commit message" in result.stdout
    assert MATCHING not in result.stdout


def test_a_token_shaped_string_fails_without_being_printed(repo):
    path, env = repo
    token = "gbx_" + "0123456789ab" + "." + "Q" * 24
    commit(path, env, "a.txt", f"token {token}\n", "Token")
    result = check(path, env)
    assert result.returncode == 1
    assert "a.txt:1" in result.stdout and token not in result.stdout


def test_no_patterns_file_fails_loudly(repo):
    path, env = repo
    env = {k: v for k, v in env.items() if k != "CABCRAWL_HOOK_PATTERNS"}
    result = check(path, env)
    assert result.returncode == 1
    assert "MISSING" in result.stdout and "NOTHING MAY BE PUSHED" in result.stdout


def test_the_patterns_file_falls_back_to_the_shared_config(repo):
    path, env = repo
    shared = os.path.join(env["HOME"], ".config", "gatbox-family")
    os.makedirs(shared)
    with open(os.path.join(shared, "patterns.local"), "w") as fh:
        fh.write(PATTERN + "\n")
    env = {k: v for k, v in env.items() if k != "CABCRAWL_HOOK_PATTERNS"}
    commit(path, env, "a.txt", f"{MATCHING}\n", "Match")
    result = check(path, env)
    assert result.returncode == 1 and "pattern #1 in a.txt:1" in result.stdout


def test_an_empty_patterns_file_fails(repo, tmp_path):
    path, env = repo
    empty = tmp_path / "empty.local"
    empty.write_text("# nothing yet\n\n")
    result = check(path, {**env, "CABCRAWL_HOOK_PATTERNS": str(empty)})
    assert result.returncode == 1 and "EMPTY" in result.stdout


def test_only_the_range_given_is_checked(repo):
    path, env = repo
    commit(path, env, "a.txt", "x\n", "Old", GIT_AUTHOR_DATE="2026-10-01T09:00:00-0500")
    base = _git(path, "rev-parse", "HEAD", env=env).strip()
    commit(path, env, "b.txt", "y\n", "New")
    assert check(path, env, f"{base}..HEAD").returncode == 0
    assert check(path, env).returncode == 1


def test_the_pre_push_hook_refuses_what_the_check_refuses(repo):
    path, env = repo
    good = _git(path, "rev-parse", "HEAD", env=env).strip()
    commit(path, env, "instance/arcade.db", "synthetic\n", "Oops")
    bad = _git(path, "rev-parse", "HEAD", env=env).strip()
    zero = "0" * 40

    def push(sha):
        # The hook finds the script through the repository it runs in.
        os.makedirs(path / "scripts", exist_ok=True)
        shutil.copy(SCRIPT, path / "scripts" / "privacy_check.sh")
        return subprocess.run(["bash", HOOK, "origin", "unused"], cwd=path, env=env,
                              input=f"refs/heads/master {sha} refs/heads/master {zero}\n",
                              capture_output=True, text=True, check=False)

    assert push(good).returncode == 0
    refused = push(bad)
    assert refused.returncode == 1 and "REFUSED" in refused.stderr


def test_code_that_handles_a_password_is_not_a_secret(repo):
    """The shared check refuses a bare password or Wi-Fi key written to the end of a line (a
    keyfile, a note), but not the code that handles one."""
    path, env = repo
    code = ("password = form.password.data\n"
            "user.set_password(password)\n"
            "create(name=name, password=new_password,\n")
    commit(path, env, "app/auth.py", code, "Handle a password")
    result = check(path, env)
    assert result.returncode == 0, result.stdout


def test_a_bare_password_in_a_note_fails_without_being_printed(repo):
    path, env = repo
    # Built in pieces, like the address above: written out, the note's line would be refused
    # from this file too.
    key, value = "pass" + "word", "Zq" + "8rTw" + "Lm3x"
    commit(path, env, "notes.md", f"{key}: {value}  # the office PC\n", "A note")
    result = check(path, env)
    assert result.returncode == 1 and "notes.md:1" in result.stdout
    assert value not in result.stdout


# One check and one hook for the three repositories, owned by GATBOX: this copy must be the same
# as any sibling checkout's. CONTRACT_SIBLINGS overrides where to look, as for the contract.
SIBLINGS = ("gatbox", "Gatbox", "arcadetech-tracker", "ArcadeTech-Tracker", "shop-hub")


def test_the_check_and_hook_are_the_same_as_the_siblings():
    parent = os.path.dirname(ROOT)
    env = os.environ.get("CONTRACT_SIBLINGS")
    repos = env.split() if env is not None else (
        [os.path.join(parent, n) for n in SIBLINGS] + [os.path.join("/opt/refs", n) for n in SIBLINGS])
    found = [r for r in repos if os.path.isfile(os.path.join(r, "scripts", "privacy_check.sh"))
             and os.path.realpath(r) != os.path.realpath(ROOT)]
    if not found:
        pytest.skip("no sibling checkout found")
    def read(path):
        with open(path, "rb") as fh:
            return fh.read()

    mine = [read(SCRIPT), read(HOOK)]
    differ = [r for r in found
              if [read(os.path.join(r, "scripts", "privacy_check.sh")),
                  read(os.path.join(r, ".githooks", "pre-push"))] != mine]
    assert not differ, "scripts/privacy_check.sh or .githooks/pre-push differs from: " + ", ".join(differ)


def test_this_repository_lists_its_own_paths():
    """What only this repository must never commit is in its .githooks/privacy.conf."""
    with open(os.path.join(ROOT, ".githooks", "privacy.conf")) as fh:
        conf = fh.read()
    assert "forbid=(^|/)(instance|backups|uploads|logs)/" in conf
    assert "forbid=(^|/)static/(maintenance_photos|profile_pics)/" in conf
