#!/usr/bin/env python3
"""Read the game library's changes for this floor's machines from GATBOX's archive (a read-only copy).

Run every ten minutes by deploy/arcade-tracker-library.timer, from the repository root, with the
application's environment (create_app reads .env):

    /opt/arcade-tracker-venv/bin/python scripts/pull_library.py

    --status    print how the last run went and what is held, then which game keys the machines name and what the
                archive has said about each (held, not at the archive, not read yet), and read nothing

Needs LIBRARY_ARCHIVE_URL and LIBRARY_ARCHIVE_TOKEN (a reader token) in .env; without them it says so and
does nothing. It prints one line of counts, which the journal keeps. Exit codes: 0 for everything, including
an archive that is out of reach (the copy is kept, the next run tries again, and the library page says when it
last refreshed); 1 when the archive refuses the token, the one answer only a person can fix.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(app=None, argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="print the last run's outcome and what is held")
    args = ap.parse_args(argv)
    if app is None:
        from app import create_app

        app = create_app()
    from app.models import LibraryRecord
    from app.services import library, library_pull

    with app.app_context():
        if not library.configured():
            print("library: no archive configured (LIBRARY_ARCHIVE_URL and LIBRARY_ARCHIVE_TOKEN): nothing to do")
            return 0
        if args.status:
            st = library.state()
            print(f"library: {LibraryRecord.query.count()} records held; last tried "
                  f"{library.ago(st.last_try) if st else 'never'}, last read {library.ago(st.last_ok) if st else 'never'}"
                  + (f"; {st.error}" if st and st.error else ""))
            for line in library.key_report_lines(library.key_report()):
                print(line)
            return 0
        out = library_pull.run()
        print("library: " + " ".join(f"{k}={len(v) if isinstance(v, list) else v}" for k, v in out.items() if k != "configured"))
        return 1 if out["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
