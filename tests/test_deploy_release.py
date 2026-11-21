"""deploy/release.sh: which commit `deploy.sh vTAG` moves to, and the releases it refuses.

Real git, in a throwaway origin and clone: a release is a tag on master's history, and going
back to one is refused while the database may have a migration that release doesn't know."""
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE = os.path.join(ROOT, "deploy", "release.sh")


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run(["git", "-c", "init.defaultBranch=master", *args], cwd=cwd, env=env,
                          check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, path, text, msg):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def release_target(clone, tag):
    """(exit code, stdout, stderr) of release_target TAG master migrations/versions/."""
    r = subprocess.run(["bash", "-c", f'. "{RELEASE}"; release_target "$1" master migrations/versions/',
                        "_", tag], cwd=clone, capture_output=True, text=True, check=False)
    return r.returncode, r.stdout.strip(), r.stderr


@pytest.fixture
def repos(tmp_path):
    """An origin with v1 (no migrations), v2 (adds one) and a side-branch tag; a clone at master's tip."""
    work = str(tmp_path / "work")
    os.makedirs(work)
    git(work, "init", "-q")
    v1 = commit(work, "app/a.py", "1\n", "one")
    git(work, "tag", "-a", "v1", "-m", "v1")
    v2 = commit(work, "migrations/versions/0001_add.py", "rev\n", "a migration")
    git(work, "tag", "-a", "v2", "-m", "v2")
    tip = commit(work, "app/a.py", "3\n", "after v2")
    git(work, "checkout", "-q", "-b", "side", v1)
    side = commit(work, "app/b.py", "x\n", "not on master")
    git(work, "tag", "-a", "vside", "-m", "side")
    git(work, "checkout", "-q", "master")
    origin = str(tmp_path / "origin.git")
    git(str(tmp_path), "clone", "-q", "--bare", work, origin)
    clone = str(tmp_path / "clone")
    git(str(tmp_path), "clone", "-q", origin, clone)
    git(clone, "fetch", "-q", "--tags", "origin", "master")
    return {"clone": clone, "v1": v1, "v2": v2, "tip": tip, "side": side}


def test_a_release_on_master_is_its_commit(repos):
    code, out, _ = release_target(repos["clone"], "v2")
    assert code == 0 and out == repos["v2"]


def test_no_such_release_is_refused(repos):
    code, out, err = release_target(repos["clone"], "v9")
    assert code == 1 and out == "" and "no release v9" in err


def test_a_tag_off_master_is_refused(repos):
    code, out, err = release_target(repos["clone"], "vside")
    assert code == 1 and out == "" and "not on the history of master" in err


def test_going_back_past_a_migration_is_refused_and_names_it(repos):
    code, out, err = release_target(repos["clone"], "v1")
    assert code == 1 and out == ""
    assert "migrations/versions/0001_add.py" in err and "Downgrade them first" in err


def test_going_back_without_crossing_a_migration_is_allowed(repos):
    git(repos["clone"], "reset", "-q", "--hard", repos["v2"])
    code, out, _ = release_target(repos["clone"], "v2")
    assert code == 0 and out == repos["v2"]


def test_deploy_sh_takes_a_release_and_moves_the_branch_to_it():
    """The wiring: the argument, the check, and a reset of the branch (not a detached HEAD)."""
    with open(os.path.join(ROOT, "deploy", "deploy.sh"), encoding="utf-8") as fh:
        text = fh.read()
    assert 'release_target "$tag" "$BRANCH" migrations/versions/' in text
    assert 'git reset --quiet --hard "$target"' in text
    assert 'git pull --ff-only origin "$BRANCH"' in text
