#!/usr/bin/env python3
"""Archive the uploaded files, next to the database dump deploy/deploy.sh takes.

    python scripts/backup_uploads.py --repo /opt/arcade-tracker \\
        --out /var/backups/arcade-tracker/arcade_tracker-uploads-<stamp>.tar.gz

The database dump holds the work orders, but the photos and images they point at are files
on disk, and nothing backed them up. deploy.sh runs this right after pg_dump, with the same
stamp, and keeps as many archives as dumps (ARCADE_KEEP).

What goes in is decided by ``backup_sources()``: these folders, relative to the checkout,
whichever exist:

  uploads/                     machine images, part photos, branding, and the work-order
                               photos (uploads/maintenance_photos/)
  static/maintenance_photos/   work-order photos still in the old public folder, until
                               scripts/move_maintenance_photos.py has moved them
  static/profile_pics/         profile pictures

Only regular files and folders are stored, under their paths relative to the checkout, so
restoring is one ``tar -xzf ARCHIVE -C /opt/arcade-tracker`` (deploy/RUNBOOK.md, "Restoring
photos and uploads"). Symbolic links are skipped, never followed: nothing outside these
folders can end up in the archive. Standard library only; it doesn't load the app or read
.env.
"""
from __future__ import annotations

import argparse
import os
import sys
import tarfile

UPLOAD_DIRS = ("uploads", "static/maintenance_photos", "static/profile_pics")


def backup_sources(repo: str) -> list[str]:
    """The upload folders under *repo* that exist (real folders, not links), relative."""
    found = []
    for rel in UPLOAD_DIRS:
        path = os.path.join(repo, rel)
        if os.path.isdir(path) and not os.path.islink(path):
            found.append(rel)
    return found


def archive_members(repo: str) -> list[str]:
    """Every folder and regular file to archive, relative to *repo*, folders first."""
    members: list[str] = []
    for rel in backup_sources(repo):
        top = os.path.join(repo, rel)
        for here, dirs, files in os.walk(top):          # os.walk doesn't follow links
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(here, d)))
            members.append(os.path.relpath(here, repo))
            for name in sorted(files):
                path = os.path.join(here, name)
                if os.path.isfile(path) and not os.path.islink(path):
                    members.append(os.path.relpath(path, repo))
    return members


def write_archive(repo: str, out: str) -> tuple[int, int]:
    """Write the archive to *out* (via a temporary name, mode 600). Returns (files, bytes)."""
    members = archive_members(repo)
    part = out + ".part"
    files = size = 0
    old_umask = os.umask(0o077)
    try:
        with tarfile.open(part, "w:gz") as tar:
            for rel in members:
                path = os.path.join(repo, rel)
                tar.add(path, arcname=rel, recursive=False)
                if os.path.isfile(path):
                    files += 1
                    size += os.path.getsize(path)
        os.replace(part, out)
    finally:
        os.umask(old_umask)
        if os.path.exists(part):
            os.remove(part)
    return files, size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="the checkout (e.g. /opt/arcade-tracker)")
    parser.add_argument("--out", required=True, help="the .tar.gz to write")
    args = parser.parse_args(argv)

    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print(f"no such folder: {repo}", file=sys.stderr)
        return 2
    if os.path.exists(args.out):
        print(f"refusing to overwrite {args.out}", file=sys.stderr)
        return 2
    sources = backup_sources(repo)
    files, size = write_archive(repo, args.out)
    print(f"    {args.out} ({files} files, {size} bytes from "
          f"{', '.join(sources) or 'no upload folders'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
