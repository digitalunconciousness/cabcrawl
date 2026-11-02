#!/usr/bin/env python3
"""Move work-order photos out of the public static folder, into uploads/ behind the login.

    python scripts/move_maintenance_photos.py            # dry run: says what it would move
    python scripts/move_maintenance_photos.py --apply    # moves them

From ``static/maintenance_photos/`` (served by the public static route until 2026-10-28) to
``uploads/maintenance_photos/`` (served only to signed-in users, by maintenance.photo_file).
The app already reads both folders, newest first, so it can be run any time after the
deploy, and run again: what is already moved is simply not there any more.

* Only names the app gives photos (``maintenance_<order>_<8 hex>.<ext>``) are moved;
  anything else is reported and left alone.
* Nothing is ever overwritten. A name already in the new folder is reported and both
  copies are kept.
* A move is a hard link and then removing the old name, so ownership, mode and dates stay
  as they were and a file is never half-copied. Across filesystems it falls back to an
  exclusive copy with the old owner, then removes the old name.

Paths come from the checkout this script is in (``--repo`` to point elsewhere); it doesn't
read .env or touch the database. Run it as root or as the service user.
"""
from __future__ import annotations

import argparse
import errno
import os
import re
import shutil
import sys

# The same pattern as app.services.storage.PHOTO_NAME (tests/test_photo_storage.py keeps
# them equal). Copied, so this runs with any python3 and without the app's settings.
PHOTO_NAME = re.compile(r"^maintenance_\d+_[0-9a-f]{8}\.(jpg|jpeg|png|gif)$", re.IGNORECASE)
OLD = os.path.join("static", "maintenance_photos")
NEW = os.path.join("uploads", "maintenance_photos")
DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _move(src: str, dst: str) -> None:
    """Move without ever replacing *dst*. Raises FileExistsError if it is there."""
    try:
        os.link(src, dst)                       # fails if dst exists: never overwrites
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        st = os.stat(src)
        with open(src, "rb") as fin, open(dst, "xb") as fout:   # "x": refuses an existing dst
            shutil.copyfileobj(fin, fout)
        shutil.copystat(src, dst)
        try:
            os.chown(dst, st.st_uid, st.st_gid)
        except PermissionError:
            pass                                # not root: the file is ours anyway
    os.unlink(src)


def plan(repo: str) -> dict[str, list[str]]:
    """What a run would do: {"move": [...], "exists": [...], "skipped": [...]} (names)."""
    old, new = os.path.join(repo, OLD), os.path.join(repo, NEW)
    out: dict[str, list[str]] = {"move": [], "exists": [], "skipped": []}
    if not os.path.isdir(old):
        return out
    for name in sorted(os.listdir(old)):
        path = os.path.join(old, name)
        if not os.path.isfile(path) or os.path.islink(path) or not PHOTO_NAME.match(name):
            out["skipped"].append(name)
        elif os.path.lexists(os.path.join(new, name)):
            out["exists"].append(name)
        else:
            out["move"].append(name)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="move (default: dry run)")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="the checkout (default: this one)")
    args = parser.parse_args(argv)

    repo = os.path.abspath(args.repo)
    old, new = os.path.join(repo, OLD), os.path.join(repo, NEW)
    todo = plan(repo)
    print(f"From {old}\nTo   {new}\n")

    moved, failed = 0, []
    if args.apply and todo["move"]:
        if not os.path.isdir(new):
            os.makedirs(new, exist_ok=True)
            try:                                        # owned like uploads/, the service's
                st = os.stat(os.path.join(repo, "uploads"))
                os.chown(new, st.st_uid, st.st_gid)
            except OSError:
                pass
        for name in todo["move"]:
            try:
                _move(os.path.join(old, name), os.path.join(new, name))
                moved += 1
            except FileExistsError:
                todo["exists"].append(name)
            except OSError as e:
                failed.append(f"{name}: {e.strerror or e}")

    for name in todo["exists"]:
        print(f"  kept both: {name} is already in the new folder")
    for name in todo["skipped"]:
        print(f"  left alone: {name} (not a work-order photo name)")
    for line in failed:
        print(f"  FAILED: {line}")
    if args.apply:
        print(f"\nMoved {moved} photo(s). Kept {len(todo['exists'])} already there, left "
              f"{len(todo['skipped'])} other file(s), {len(failed)} failed.")
    else:
        print(f"\nWould move {len(todo['move'])} photo(s). {len(todo['exists'])} already "
              f"there, {len(todo['skipped'])} other file(s). Nothing was changed; re-run "
              "with --apply to move them.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
