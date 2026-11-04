#!/usr/bin/env python3
"""Check the settings in .env (and the environment) before the app runs with them.

    python scripts/check_config.py           # summary, warnings, errors; exit 1 on an error

deploy.sh runs this against the new code before it migrates or restarts, so a setting the
new version can't run with stops the deploy while the old version is still serving. The
checks themselves are app/config.py's. It prints no secret: only whether one is set.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    from dotenv import load_dotenv

    from app.config import ConfigError, load

    load_dotenv()
    try:
        s = load()
    except ConfigError as e:
        print("CONFIGURATION PROBLEMS (the app will not start with these):")
        print(e)
        return 1

    def is_set(value: str) -> str:
        return "set" if value else "not set"

    from sqlalchemy.engine import make_url

    db = make_url(s.database_url)
    print(f"database     : {db.get_backend_name()} {db.host or db.database or ''}".rstrip())
    print(f"SECRET_KEY   : set ({len(s.secret_key)} characters)")
    print(f"BASE_URL     : {s.base_url or 'not set'}")
    print(f"LOG_LEVEL    : {s.log_level}")
    print(f"photos       : at most {s.max_photos_per_record} per order, {s.max_storage_mb} MB in all"
          f"{', copied to S3' if s.cloud_photos else ''}")
    print(f"EASYPOST     : {is_set(s.easypost_api_key)}")
    print(f"library      : {s.library_url + (' via Access' if s.library_access_id else '') if s.library_url else 'no archive configured'}")
    for warning in s.warnings:
        print(f"WARNING: {warning}")
    print("configuration: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
