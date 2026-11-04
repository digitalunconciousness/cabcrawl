"""Every setting the app reads from the environment, checked once at startup (Step 5.6).

Before this, settings were read wherever they were used: some at import, some per request, some
documented in .env.example and never read at all. A bad value surfaced as a 500 halfway through
someone's shift, or never (a malformed BASE_URL quietly printed QR labels with the wrong host).

``load()`` reads them all and returns every problem at once, in two kinds:

* **errors** stop the app, because running wrong is worse than not running: no SECRET_KEY, a
  database URL that can't be parsed, a BASE_URL that labels would print wrongly;
* **warnings** are logged and the app runs, with the feature that needs the setting off.

``scripts/check_config.py`` runs the same checks, and deploy.sh runs that against the new code
before it migrates or restarts, so a bad .env stops a deploy while the old version still serves.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

PLACEHOLDER_SECRET = "your-secret-key-change-this-for-production"   # .env.example's value
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
DEFAULT_SOURCE = "https://github.com/digitalunconciousness/cabcrawl"
TRUE, FALSE = ("1", "true", "yes", "on"), ("", "0", "false", "no", "off")


class ConfigError(RuntimeError):
    """The settings can't be run with. The message lists every problem, one per line."""


@dataclass(frozen=True)
class Settings:
    secret_key: str
    database_url: str
    base_url: str                   # "" = labels use the address the request came in on
    source_url: str
    log_level: str
    backups_dir: str                # where deploy.sh writes its pg_dump files
    max_photos_per_record: int
    max_storage_mb: int
    cloud_photos: bool              # copy work-order photos to S3 (they are still served locally)
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_bucket: str
    aws_region: str
    easypost_api_key: str           # "" = live parcel tracking is off
    # The library (GATBOX's archive): a read-only copy of the games the machines name. "" = no archive
    # configured, so the pull job does nothing and the pages have no library section.
    library_url: str = ""
    library_token: str = ""         # a reader token (gbx_<id>.<secret>): read-only, issued per app by the archive
    library_access_id: str = ""     # Cloudflare Access service token, only when the archive is reached through the tunnel
    library_access_secret: str = ""
    library_max_file_mb: int = 100
    warnings: tuple[str, ...] = field(default=(), compare=False)

    def apply(self, config) -> None:
        """Copy into a Flask config, under the keys the code reads."""
        config.update(
            SECRET_KEY=self.secret_key,
            SQLALCHEMY_DATABASE_URI=self.database_url,
            BASE_URL=self.base_url,
            SOURCE_URL=self.source_url,
            LOG_LEVEL=self.log_level,
            BACKUPS_DIR=self.backups_dir,
            MAX_PHOTOS_PER_RECORD=self.max_photos_per_record,
            MAX_STORAGE_MB=self.max_storage_mb,
            CLOUD_PHOTOS=self.cloud_photos,
            AWS_ACCESS_KEY_ID=self.aws_access_key_id,
            AWS_SECRET_ACCESS_KEY=self.aws_secret_access_key,
            AWS_BUCKET_NAME=self.aws_bucket,
            AWS_REGION=self.aws_region,
            EASYPOST_API_KEY=self.easypost_api_key,
            LIBRARY_URL=self.library_url,
            LIBRARY_TOKEN=self.library_token,
            LIBRARY_ACCESS_ID=self.library_access_id,
            LIBRARY_ACCESS_SECRET=self.library_access_secret,
            LIBRARY_MAX_FILE=self.library_max_file_mb * 1024 * 1024,
        )


def _placeholder(value: str) -> bool:
    """.env.example's dummy values ("your-...-here") count as unset."""
    v = value.lower()
    return v.startswith("your-") or v.endswith("-here")


def load(env: Mapping[str, str] | None = None, *, default_db: str = "sqlite:///arcade.db") -> Settings:
    env = os.environ if env is None else env
    errors: list[str] = []
    warnings: list[str] = []

    def get(name: str, default: str = "") -> str:
        return (env.get(name) or default).strip()

    def number(name: str, default: int, low: int) -> int:
        raw = get(name, str(default))
        try:
            value = int(raw)
        except ValueError:
            errors.append(f"{name}={raw!r} must be a whole number")
            return default
        if value < low:
            errors.append(f"{name}={value} must be at least {low}")
        return value

    def flag(name: str) -> bool:
        raw = get(name).lower()
        if raw not in TRUE + FALSE:
            errors.append(f"{name}={raw!r} must be true or false")
        return raw in TRUE

    # SECRET_KEY: signs sessions. A new one signs everyone out, so it must be set and kept (F-22).
    secret = get("SECRET_KEY")
    if not secret or secret == PLACEHOLDER_SECRET:
        errors.append("SECRET_KEY is not set. Put one in .env, for example the output of\n"
                      "  python -c \"import secrets; print(secrets.token_hex(32))\"\n"
                      "Keep it: changing it signs everyone out.")
    elif len(secret) < 32:
        warnings.append(f"SECRET_KEY is only {len(secret)} characters; 32 or more is safer. "
                        "Changing it signs everyone out, so pick a quiet moment.")

    # DATABASE_URL
    database_url = get("DATABASE_URL", default_db)
    try:
        from sqlalchemy.engine import make_url

        backend = make_url(database_url).get_backend_name()
        if backend not in ("postgresql", "sqlite"):
            errors.append(f"DATABASE_URL uses {backend!r}; only PostgreSQL and SQLite are supported")
    except Exception:  # noqa: BLE001 -- any parse failure is the same answer
        errors.append("DATABASE_URL can't be read as a database URL")

    # BASE_URL: printed into every QR label, so a wrong value is a sheet of wrong labels.
    base_url = get("BASE_URL").rstrip("/")
    if base_url:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            errors.append(f"BASE_URL={base_url!r} must start with https:// (or http://) and name a host")
        elif parts.path or parts.query or parts.fragment:
            errors.append(f"BASE_URL={base_url!r} must be just the address, like "
                          "https://tracker.example.org, with nothing after the host")
        elif parts.hostname and parts.hostname.endswith(("example.com", "example.org")):
            errors.append(f"BASE_URL={base_url!r} is still the example; set your public address")
    else:
        warnings.append("BASE_URL is not set, so labels encode whatever address they were printed "
                        "from. Set it to the public address before printing labels.")

    source_url = get("SOURCE_URL", DEFAULT_SOURCE).rstrip("/")
    if urlsplit(source_url).scheme not in ("http", "https"):
        errors.append(f"SOURCE_URL={source_url!r} must be a web address (the footer links to it)")

    log_level = get("LOG_LEVEL", "INFO").upper()
    if log_level not in LOG_LEVELS:
        errors.append(f"LOG_LEVEL={log_level!r} must be one of {', '.join(LOG_LEVELS)}")

    # Optional S3 copy of work-order photos. Missing credentials switch it off, not the app.
    cloud = flag("USE_CLOUD_STORAGE")
    aws = {k: get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_BUCKET_NAME")}
    aws = {k: ("" if _placeholder(v) else v) for k, v in aws.items()}
    if cloud and not all(aws.values()):
        missing = ", ".join(k for k, v in aws.items() if not v)
        warnings.append(f"USE_CLOUD_STORAGE is on but {missing} is not set; photos are kept "
                        "on this server only.")
        cloud = False

    easypost = get("EASYPOST_API_KEY")
    easypost = "" if _placeholder(easypost) else easypost

    # The library archive. The address is the archive's private address (http on the node's own network is
    # fine: the token is read-only and the traffic doesn't leave it) or its tunnel address (https, with the
    # Cloudflare Access service token pair). One without the other is a mistake, said at startup.
    lib_url = get("LIBRARY_ARCHIVE_URL").rstrip("/")
    lib_token = get("LIBRARY_ARCHIVE_TOKEN")
    lib_id, lib_secret = get("LIBRARY_ACCESS_CLIENT_ID"), get("LIBRARY_ACCESS_CLIENT_SECRET")
    if lib_url:
        parts = urlsplit(lib_url)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.path or parts.query or parts.fragment:
            errors.append(f"LIBRARY_ARCHIVE_URL={lib_url!r} must be just an address, like http://archive.example.test:8081 "
                          "or https://archive.example.org")
        if lib_token and not (lib_token.startswith("gbx_") and "." in lib_token):
            errors.append("LIBRARY_ARCHIVE_TOKEN must be a reader token of the form gbx_<id>.<secret>")
        if not lib_token:
            warnings.append("LIBRARY_ARCHIVE_URL is set but LIBRARY_ARCHIVE_TOKEN is not, so the library is not read.")
    elif lib_token or lib_id or lib_secret:
        warnings.append("LIBRARY_ARCHIVE_TOKEN or the Access service token is set but LIBRARY_ARCHIVE_URL is not, "
                        "so the library is not read.")
    if bool(lib_id) != bool(lib_secret):
        warnings.append("LIBRARY_ACCESS_CLIENT_ID and LIBRARY_ACCESS_CLIENT_SECRET go together; neither is sent.")
        lib_id = lib_secret = ""
    if not lib_url or not lib_token:
        lib_url = lib_token = lib_id = lib_secret = ""          # half a setting is no setting

    settings = Settings(
        secret_key=secret,
        database_url=database_url,
        base_url=base_url,
        source_url=source_url,
        log_level=log_level,
        backups_dir=get("ARCADE_BACKUPS", "/var/backups/arcade-tracker"),
        max_photos_per_record=number("MAX_PHOTOS_PER_RECORD", 10, 1),
        max_storage_mb=number("MAX_TOTAL_STORAGE_MB", 500, 1),
        cloud_photos=cloud,
        aws_access_key_id=aws["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=aws["AWS_SECRET_ACCESS_KEY"],
        aws_bucket=aws["AWS_BUCKET_NAME"],
        aws_region=get("AWS_REGION", "us-east-1"),
        easypost_api_key=easypost,
        library_url=lib_url,
        library_token=lib_token,
        library_access_id=lib_id,
        library_access_secret=lib_secret,
        library_max_file_mb=number("LIBRARY_MAX_FILE_MB", 100, 1),
        warnings=tuple(warnings),
    )
    if errors:
        raise ConfigError("\n".join(f"- {e}" for e in errors))
    return settings
