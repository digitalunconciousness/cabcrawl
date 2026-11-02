#!/usr/bin/env bash
# Deploy arcade-tracker on the server. Backs the database and the uploaded files
# up first, then pulls, installs, migrates, restarts and checks that the site
# answers. Stops at the first failure and never carries on past a bad step.
#
#   deploy/deploy.sh            # deploy          (run as root)
#   deploy/deploy.sh --check    # preflight only: change nothing
#
# The database is PostgreSQL on another host, so the backup is a pg_dump, not a
# file copy. DATABASE_URL comes from .env and is never printed.
set -euo pipefail

REPO=${ARCADE_REPO:-/opt/arcade-tracker}
VENV=${ARCADE_VENV:-/opt/arcade-tracker-venv}
BACKUPS=${ARCADE_BACKUPS:-/var/backups/arcade-tracker}
SERVICE=${ARCADE_SERVICE:-arcade-tracker}
# Liveness gate: the thinnest path that proves the app is serving. (The skeeball
# health probe that used to be reported alongside it was retired with skeeball on
# 2026-10-08; /api/v1/health is the unauthenticated probe now.)
HEALTH=${ARCADE_HEALTH:-http://127.0.0.1:5000/}
BRANCH=${ARCADE_BRANCH:-master}
KEEP=${ARCADE_KEEP:-14}

check_only=0
[ "${1:-}" = "--check" ] && check_only=1

step() { printf '\n==> %s\n' "$*"; }
die()  { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

# --- preflight ---------------------------------------------------------------
step "Preflight"
[ "$(id -u)" = 0 ] || die "run as root (the container has no sudo; pct enter lands you as root)"
[ -d "$REPO/.git" ] || die "$REPO is not a git checkout"
[ -f "$REPO/.env" ] || die "$REPO/.env is missing (DATABASE_URL, SECRET_KEY)"
[ -x "$VENV/bin/python" ] || die "no virtualenv at $VENV -- build it first (see the cut-over runbook)"
command -v pg_dump >/dev/null || die "pg_dump not found: apt-get install postgresql-client"
command -v curl >/dev/null || die "curl not found: apt-get install curl"
systemctl list-unit-files "$SERVICE.service" >/dev/null 2>&1 || die "no $SERVICE.service installed"

cd "$REPO"
# Only MODIFIED TRACKED files block `git pull --ff-only`; untracked ones are
# harmless. Checking both refused a deploy over two leftover files that had
# already been folded into the repository, so the check is narrowed and untracked
# files are reported instead.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git status --short --untracked-files=no | sed 's/^/    /'
    die "tracked files have local changes; commit, stash or discard them first"
fi
untracked=$(git ls-files --others --exclude-standard)
if [ -n "$untracked" ]; then
    printf '    note: untracked files present (they do not block the pull):\n'
    printf '%s\n' "$untracked" | sed 's/^/      /'
fi
current=$(git rev-parse --abbrev-ref HEAD)
[ "$current" = "$BRANCH" ] || die "on branch '$current', expected '$BRANCH' (set ARCADE_BRANCH to override)"

# shellcheck disable=SC1091
set -a; . "$REPO/.env"; set +a
[ -n "${DATABASE_URL:-}" ] || die "DATABASE_URL is not set in .env"
# Never print the URL: it carries the password.
printf '    database host : %s\n' "${DATABASE_URL##*@}"
printf '    repo          : %s (%s, %s)\n' "$REPO" "$current" "$(git rev-parse --short HEAD)"
printf '    venv          : %s (%s)\n' "$VENV" "$("$VENV/bin/python" -V)"
pg_dump --version | sed 's/^/    pg_dump       : /'

# pg_dump refuses to dump from a server newer than itself ("aborting because of
# server version mismatch"), and Debian's postgresql-client tracks the distro, not
# the server. Catch that here rather than at the dump, where the message is less
# obvious and the deploy has already started.
server_major=$(psql "$DATABASE_URL" -tAc "SELECT current_setting('server_version_num')::int / 10000" 2>/dev/null | tr -d '[:space:]')
dump_major=$(pg_dump --version | sed -nE 's/^pg_dump \(PostgreSQL\) ([0-9]+).*/\1/p')
if [ -z "$server_major" ]; then
    die "could not reach the database to read its version; check DATABASE_URL and that the server is up"
fi
printf '    server major  : %s\n' "$server_major"
if [ "$dump_major" -lt "$server_major" ]; then
    printf '\n' >&2
    printf 'pg_dump is %s but the server is %s. pg_dump cannot dump from a newer\n' "$dump_major" "$server_major" >&2
    printf 'server, so the backup -- and therefore this deploy -- would fail.\n\n' >&2
    printf 'Install a matching client from the PostgreSQL APT repository:\n' >&2
    printf '  apt-get install -y curl ca-certificates\n' >&2
    printf '  install -d /usr/share/postgresql-common/pgdg\n' >&2
    printf '  curl -fsS https://www.postgresql.org/media/keys/ACCC4CF8.asc \\\n' >&2
    printf '    -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc\n' >&2
    printf '  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc]" \\\n' >&2
    printf '       "https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo "$VERSION_CODENAME")-pgdg main" \\\n' >&2
    printf '    > /etc/apt/sources.list.d/pgdg.list\n' >&2
    printf '  apt-get update && apt-get install -y postgresql-client-%s\n' "$server_major" >&2
    die "pg_dump $dump_major is too old for server $server_major"
fi
printf '    health        : %s\n' "$HEALTH"

if [ "$check_only" = 1 ]; then
    step "Preflight only (--check): nothing was changed."
    exit 0
fi

# --- 1. back the database up -------------------------------------------------
step "Backing the database up"
mkdir -p "$BACKUPS"; chmod 700 "$BACKUPS"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dump="$BACKUPS/arcade_tracker-$stamp.sql.gz"
# --clean --if-exists so the dump can be replayed over an existing database.
pg_dump --clean --if-exists --no-owner --no-privileges "$DATABASE_URL" | gzip -9 > "$dump" \
    || die "pg_dump failed; nothing has changed yet"
size=$(stat -c %s "$dump")
# A dump of a populated database is far bigger than this; a few hundred bytes
# means pg_dump "succeeded" against nothing useful.
[ "$size" -gt 2048 ] || die "the dump is only $size bytes -- refusing to deploy over a database we cannot restore ($dump)"
printf '    %s (%s bytes)\n' "$dump" "$size"
chmod 600 "$dump"

# --- 1b. back the uploads up ---------------------------------------------------
# The dump holds the work orders; their photos, the machine and part images and the
# branding are files under uploads/ (and, until scripts/move_maintenance_photos.py has
# run, static/maintenance_photos/). Archived with the same stamp, kept as long as the
# dumps. scripts/backup_uploads.py decides what goes in; the RUNBOOK says how to restore.
step "Backing the uploads up"
uploads="$BACKUPS/arcade_tracker-uploads-$stamp.tar.gz"
"$VENV/bin/python" scripts/backup_uploads.py --repo "$REPO" --out "$uploads" \
    || die "the uploads backup failed; nothing has changed yet (the database dump is $dump)"
chmod 600 "$uploads"

# --- 2. pull ------------------------------------------------------------------
step "Pulling $BRANCH"
before=$(git rev-parse HEAD)
git pull --ff-only origin "$BRANCH" || die "git pull --ff-only refused; the branches have diverged"
after=$(git rev-parse HEAD)
if [ "$before" = "$after" ]; then
    printf '    already up to date (%s)\n' "$(git rev-parse --short HEAD)"
else
    git --no-pager log --oneline "$before..$after" | sed 's/^/    /'
fi

# --- 3. dependencies ----------------------------------------------------------
step "Installing dependencies into $VENV"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r requirements.txt \
    || die "pip install failed"
# The driver is the thing most likely to be missing or mismatched; prove it imports.
"$VENV/bin/python" -c "import sqlalchemy, psycopg2; print('    SQLAlchemy', sqlalchemy.__version__, 'psycopg2', psycopg2.__version__.split()[0])" \
    || die "the database driver does not import; the application cannot reach PostgreSQL"

# --- 4. migrate ---------------------------------------------------------------
step "Checking the configuration"
# With the new code, before anything changes: a setting it can't run with stops the deploy
# here, while the old version is still serving (Step 5.6).
if ! ( cd "$REPO" && "$VENV/bin/python" scripts/check_config.py ); then
    die "fix .env (see above), then run deploy.sh again; nothing was migrated or restarted"
fi

step "Applying migrations"
( cd "$REPO" && "$VENV/bin/flask" --app run:app db upgrade ) || die "flask db upgrade failed; the database may be half-migrated -- restore from $dump"

# --- 5. restart ---------------------------------------------------------------
# --- 4b. the schema must actually match the models now ----------------------
# `flask db upgrade` succeeding is not proof: if the database was ever stamped
# ahead of its real schema, upgrade is a no-op and the missing columns stay
# missing until a page touches one and 500s. This is the post-condition.
# It also checks the identity sequences: one left behind its table serves every
# page and fails every insert, so the service must not come back on it either.
step "Checking the schema against the models"
if ! ( cd "$REPO" && "$VENV/bin/python" scripts/check_schema.py ); then
    die "the database is not fit to serve (see above); the service was NOT restarted"
fi

step "Restarting $SERVICE"
systemctl restart "$SERVICE" || die "systemctl restart failed; see journalctl -u $SERVICE"

# --- 6. health ----------------------------------------------------------------
step "Health check"
ok=0
for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS --max-time 5 "$HEALTH" >/dev/null 2>&1; then ok=1; break; fi
    sleep 2
    printf '    waiting (%s/10)\n' "$i"
done
if [ "$ok" != 1 ]; then
    printf '\n'
    systemctl --no-pager --lines=20 status "$SERVICE" || true
    printf '\nTo roll back:\n'
    printf '  git -C %s reset --hard %s\n' "$REPO" "$before"
    printf '  gunzip -c %s | psql "$DATABASE_URL"\n' "$dump"
    printf '  systemctl restart %s\n' "$SERVICE"
    die "the service did not answer $HEALTH"
fi
printf '    answered OK\n'

# --- 7. prune old dumps -------------------------------------------------------
step "Keeping the newest $KEEP dumps and uploads archives"
ls -1t "$BACKUPS"/arcade_tracker-*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f -- "$old"; printf '    removed %s\n' "$(basename "$old")"
done
ls -1t "$BACKUPS"/arcade_tracker-uploads-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f -- "$old"; printf '    removed %s\n' "$(basename "$old")"
done

step "Deployed $(git rev-parse --short HEAD) and the site answers."
