# Server runbook

Everything here runs **on the server** (the arcade-tracker LXC) **as root**.
`pct enter 131` from the Proxmox host lands you as root, and the container has no
`sudo` installed, so the commands below carry no `sudo` prefix. If you reach the
box some other way as a non-root user, prefix each one yourself.

The repository is at `/opt/arcade-tracker`; the virtualenv it uses is deliberately
*outside* that directory.

**The name.** The app is called Cabcrawl since October 2026 (it was Arcade Tracker). Only what
people read changed. On the server everything keeps its old name, on purpose, and nothing here
needs renaming: `arcade-tracker.service` and the alert timer, `/opt/arcade-tracker`, the
virtualenv and backup paths, the database and its role, the cookie and environment variable
names, the hostname and tunnel, and every URL (printed QR labels use `/g/<barcode>`). Renaming
any of them would be a migration of its own, not part of the rename.

The GitHub repository is `digitalunconciousness/cabcrawl` now. GitHub forwards the old address,
but point the server's checkout at the new one once:

```bash
git -C /opt/arcade-tracker remote set-url origin https://github.com/digitalunconciousness/cabcrawl.git
```

## Routine deploy

```bash
/opt/arcade-tracker/deploy/deploy.sh --check    # preflight, changes nothing
/opt/arcade-tracker/deploy/deploy.sh
```

It backs the database up with `pg_dump` before touching anything, refuses to
continue if the dump is too small to restore from, and stops at the first
failure. If the health check fails it prints the exact rollback commands.

Beside each dump it archives the uploaded files with the same stamp,
`arcade_tracker-uploads-<stamp>.tar.gz`: everything under `uploads/` (work-order photos,
machine and part images, branding), any photos still in `static/maintenance_photos/`, and
`static/profile_pics/`. `scripts/backup_uploads.py` decides what goes in. It keeps the newest
`ARCADE_KEEP` (14) of each, so budget roughly 14 times the size of those folders in
`/var/backups/arcade-tracker` (Admin, Storage shows the photos' share). Restoring is under
"Restoring photos and uploads".

## One-time: Secure cookies through the tunnel (Step 5.1)

The app now refuses to start without `SECRET_KEY`, and marks its cookies `Secure` on HTTPS
requests. waitress only tells it a tunnel request was HTTPS once the unit file trusts
loopback, and `deploy.sh` does not install the unit file, so do this once:

```bash
# 1. Before deploying: the key must be there, or the new code won't start.
grep -q '^SECRET_KEY=.' /opt/arcade-tracker/.env && echo "SECRET_KEY ok"

# 2. Deploy as usual.
/opt/arcade-tracker/deploy/deploy.sh

# 3. Install the new unit (it adds --trusted-proxy=127.0.0.1) and restart.
cp /opt/arcade-tracker/arcade-tracker.service /etc/systemd/system/arcade-tracker.service
systemctl daemon-reload && systemctl restart arcade-tracker

# 4. Check: through the tunnel the cookies say Secure; on the LAN address they don't.
curl -s -o /dev/null -D - https://<public-hostname>/login | grep -i set-cookie
```

Between steps 2 and 3 nothing breaks: the cookies are simply not `Secure` yet, as before.
Nobody is signed out unless `SECRET_KEY` changes.

## One-time: move work-order photos behind the login (2026-10-28) ⏸

Work-order photos used to be saved in `static/maintenance_photos/`, which the app served to
anyone with the address. New ones go in `uploads/maintenance_photos/` and every photo is now
shown only to signed-in users; the old addresses redirect to the login. The app reads both
folders, so deploying first breaks nothing. Then move the old files across, as the service
user so they keep their owner. The script never overwrites a file, and a second run finds
nothing left to do.

```bash
# 1. Deploy as usual.
/opt/arcade-tracker/deploy/deploy.sh

# 1b. Archive the uploads by hand this once. The deploy that brings this change runs the
#     deploy.sh that was on disk before the pull, which doesn't archive them yet; every
#     deploy after it does.
cd /opt/arcade-tracker
/opt/arcade-tracker-venv/bin/python scripts/backup_uploads.py --repo /opt/arcade-tracker \
  --out "/var/backups/arcade-tracker/arcade_tracker-uploads-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
chmod 600 /var/backups/arcade-tracker/arcade_tracker-uploads-*.tar.gz

# 2. Dry run: lists what it would move. Changes nothing.
cd /opt/arcade-tracker
runuser -u arcade-tracker -- /opt/arcade-tracker-venv/bin/python scripts/move_maintenance_photos.py

# 3. Move them.
runuser -u arcade-tracker -- /opt/arcade-tracker-venv/bin/python scripts/move_maintenance_photos.py --apply

# 4. Check: the old folder is empty (or holds only files it said it left alone), and an old
#    address now answers 302 to /maintenance/photos/<name>, which asks for a login.
ls /opt/arcade-tracker/static/maintenance_photos | wc -l
name=$(ls /opt/arcade-tracker/uploads/maintenance_photos | head -1)
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' "http://127.0.0.1:5000/static/maintenance_photos/$name"
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' "http://127.0.0.1:5000/maintenance/photos/$name"
```

Leave the empty `static/maintenance_photos/` folder in place: the unit's `ReadWritePaths` names
it, and systemd won't start the service if it is missing. Admin, Storage counts any photo still
in the old folder.

## One-time: alerts (feature step 6)

Alerts are written to the database as things happen; a systemd timer runs
`scripts/send_alerts.py` every minute to deliver them. Deploy as usual first (it runs the
migration), then install the timer:

```bash
cp /opt/arcade-tracker/deploy/arcade-tracker-alerts.service /etc/systemd/system/
cp /opt/arcade-tracker/deploy/arcade-tracker-alerts.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now arcade-tracker-alerts.timer
systemctl list-timers arcade-tracker-alerts.timer      # next run within a minute
journalctl -u arcade-tracker-alerts -n 5               # "released=0 summaries=0 sent=0 failed=0"
```

Nothing is sent until an admin adds a channel (Admin, Alerts). Set the opening hours on the
same page: without them alerts are never held and there is no daily summary.

### The ntfy server (its own LXC)

Staff phones need to reach it from anywhere, so it gets its own hostname through Cloudflare
(e.g. `ntfy.<your-domain>`) from a small tunnel of its own in the same LXC; the tracker's
tunnel is left alone. Done this way on 2026-10-26; the steps below are what worked.

**1. A Debian LXC** (unprivileged, 1 CPU, 512 MB, 4 GB, fixed LAN address). As root, ntfy
from its own repository (moved to archive.ntfy.sh in September 2025):

```bash
apt update && apt install -y curl
mkdir -p /etc/apt/keyrings
curl -L -o /etc/apt/keyrings/ntfy.gpg https://archive.ntfy.sh/apt/keyring.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/ntfy.gpg] https://archive.ntfy.sh/apt stable main" \
    > /etc/apt/sources.list.d/ntfy.list
apt update && apt install -y ntfy
```

**2. `/etc/ntfy/server.yml`:**

```yaml
base-url: "https://ntfy.<your-domain>"
listen-http: "127.0.0.1:80"             # only the tunnel, on this box, needs to reach it
behind-proxy: true                      # the tunnel connects from loopback
auth-file: "/var/lib/ntfy/user.db"
auth-default-access: "deny-all"         # nobody reads or posts without an account
enable-login: true                      # without it the web page has no way to sign in
require-login: true                     # and with it, the web page asks first
# iPhones only get instant pushes through ntfy.sh: this server sends it the message id and
# a hash of the topic address, never the message itself.
upstream-base-url: "https://ntfy.sh"
```

**3. Start it before adding accounts.** The account database is created on the first start;
`ntfy user add` before then says "auth-file does not exist" and adds nothing.

```bash
systemctl enable --now ntfy && sleep 2
ntfy user add tracker                       # the tracker posts as this user
ntfy access tracker "arcade-*" write-only
ntfy token add tracker                      # the tk_... token goes in each alert channel
ntfy user add staff                         # one shared login for the phones (or one each)
ntfy access staff "arcade-*" read-only
ntfy access                                 # check: tracker write-only, staff read-only
```

The token is a password for posting: keep it out of chats and tickets. To replace one,
`ntfy token remove tracker tk_...` then `ntfy token add tracker`.

**4. The tunnel, in the same LXC** (Cloudflare's apt repository):

```bash
mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
    > /etc/apt/sources.list.d/cloudflared.list
apt-get update && apt-get install -y cloudflared
cloudflared tunnel login                    # open the link it prints, pick your domain
cloudflared tunnel create ntfy
cloudflared tunnel list                     # the tunnel's ID
```

`/root/.cloudflared/config.yml`, with the real ID in **both** places (`ingress validate`
doesn't check the credentials path, and a placeholder left there stops the service starting):

```yaml
tunnel: <ID>
credentials-file: /root/.cloudflared/<ID>.json
ingress:
  - hostname: ntfy.<your-domain>
    service: http://127.0.0.1:80
  - service: http_status:404
```

```bash
cloudflared tunnel route dns ntfy ntfy.<your-domain>
cloudflared tunnel ingress validate
cloudflared --config /root/.cloudflared/config.yml service install
systemctl enable --now cloudflared && systemctl status cloudflared --no-pager
```

`service install` copies the config to `/etc/cloudflared/config.yml`, and that copy is what
runs: edit that one afterwards, then `systemctl restart cloudflared`. Don't put the ntfy
hostname behind Cloudflare Access: the phone app can't get past its login.

**5. Check it from the tracker's LXC:**

```bash
curl -H "Authorization: Bearer tk_..." -d "hello from the tracker" https://ntfy.<your-domain>/arcade-test
```

`{"id":...` back means it works; 401 is a wrong token, 403 a missing access rule, a
Cloudflare error page a tunnel that isn't running.

**6. Phones and channels.** Use topic names that start with `arcade-`, e.g.
`https://ntfy.<your-domain>/arcade-techs` and `.../arcade-manager`. On each phone: install
ntfy, add the server address with the staff login, subscribe to the topics that person wants.
In the tracker (Admin, Alerts), add a channel per topic with the token, then press **Send a
test**.

## One-time cut-over

Do this **once**, in this order.

The order is load-bearing for two reasons. The pull is the commit that untracks
`venv/`, so the service has to be running from the external virtualenv *before* it
happens, or the deploy deletes the interpreter out from under itself. And the new
unit and `requirements.txt` only arrive *with* that pull — so they are read
straight out of the fetched objects first, which changes nothing in the working
tree.

```bash
# 1. Fetch, but do not touch the working tree. The running service is unaffected.
cd /opt/arcade-tracker
git fetch origin master
git log --oneline HEAD..origin/master        # what is about to land

# 2. Read the new files out of the fetched commit without checking anything out.
git show origin/master:requirements.txt > /tmp/requirements.txt
git show origin/master:arcade-tracker.service > /tmp/arcade-tracker.service
git show origin/master:deploy/listen.conf.example > /tmp/listen.conf

# 3. The service account, and only the paths it must write.
#
#    Do NOT chown the whole checkout. Two reasons: git refuses to work in a
#    repository owned by another user ("detected dubious ownership"), which breaks
#    the pull below and every git call in deploy.sh; and a web service should not
#    be able to rewrite the code it is executing. The code stays root-owned and
#    the service user owns only what it writes -- the same paths the unit lists in
#    ReadWritePaths.
adduser --system --group --no-create-home --home /opt/arcade-tracker arcade-tracker

# All six that app/__init__.py:_create_directories creates at startup. They must
# exist, be writable by the service user, AND be listed in the unit's
# ReadWritePaths -- ProtectSystem=strict makes everything else read-only.
for d in instance uploads backups logs static/maintenance_photos static/profile_pics; do
  install -d -o arcade-tracker -g arcade-tracker -m 755 "/opt/arcade-tracker/$d"
  chown -R arcade-tracker:arcade-tracker "/opt/arcade-tracker/$d"
done

# .env holds DATABASE_URL and SECRET_KEY: root owns it, the service only reads it.
# Since Step 5.1 the app refuses to start without SECRET_KEY (it used to make a random one,
# signing everyone out on each restart). Keep the existing value; a new one signs everyone out.
#   grep -q '^SECRET_KEY=.' /opt/arcade-tracker/.env || echo "SECRET_KEY missing"
chown root:arcade-tracker /opt/arcade-tracker/.env
chmod 640 /opt/arcade-tracker/.env

# 4. The external virtualenv, from the new pinned requirements.
apt-get update && sudo apt-get install -y python3-venv postgresql-client
python3 -m venv /opt/arcade-tracker-venv
/opt/arcade-tracker-venv/bin/pip install -U pip
/opt/arcade-tracker-venv/bin/pip install -r /tmp/requirements.txt

# 5. Prove it can reach the database BEFORE switching the unit over. If this
#    fails, stop: nothing has changed yet and the old service is still running.
cat > /tmp/dbcheck.py <<'DBCHECK'
import os, sqlalchemy as sa
url = os.environ["DATABASE_URL"]
print("target  :", url.split("@")[-1])          # no credentials
engine = sa.create_engine(url)
with engine.connect() as c:
    print("server  :", c.execute(sa.text("select version()")).scalar()[:40])
    print("games   :", c.execute(sa.text("select count(*) from game")).scalar())
    print("maint   :", c.execute(sa.text("select count(*) from maintenance_record")).scalar())
    print("users   :", c.execute(sa.text('select count(*) from "user"')).scalar())
DBCHECK
chmod 644 /tmp/dbcheck.py
runuser -u arcade-tracker -- bash -c 'set -a; . /opt/arcade-tracker/.env; set +a; \
  exec /opt/arcade-tracker-venv/bin/python /tmp/dbcheck.py'

# 6. The listen addresses. Loopback is what the tunnel uses; add this host's LAN
#    address so GATBOX can reach the hub directly instead of going out to
#    Cloudflare and back. Never 0.0.0.0.
install -d -m 755 /etc/arcade-tracker
cp /tmp/listen.conf /etc/arcade-tracker/listen.conf
$EDITOR /etc/arcade-tracker/listen.conf

# 7. Switch the unit over.
cp /tmp/arcade-tracker.service /etc/systemd/system/arcade-tracker.service
systemctl daemon-reload
systemctl restart arcade-tracker

# 8. Verify BEFORE pulling.
systemctl --no-pager status arcade-tracker
# Wait for it to bind. `systemctl restart` returns as soon as the process is
# forked, and waitress needs a moment to import the application, so curling
# immediately reports "Couldn't connect" on a service that is perfectly fine.
for i in $(seq 20); do
  curl -fsS --max-time 2 http://127.0.0.1:5000/ >/dev/null 2>&1 && { echo "  serving OK"; break; }
  sleep 1
done
ss -tlnp | grep ':5000'      # expect 127.0.0.1 and the LAN address, NOT 0.0.0.0
# And load the site in a browser, through the tunnel, before going further.

# 9. The old virtualenv is now dead weight, and it is the reason the tree is
#    dirty: it was rebuilt in place with Python 3.11 over a committed 3.12 one,
#    and git will not delete locally-modified files.
rm -rf /opt/arcade-tracker/venv
git -C /opt/arcade-tracker status --porcelain     # expect only the two untracked files below

# 10. Now pull.
git -C /opt/arcade-tracker pull --ff-only origin master
systemctl restart arcade-tracker
# Wait for it to bind. `systemctl restart` returns as soon as the process is
# forked, and waitress needs a moment to import the application, so curling
# immediately reports "Couldn't connect" on a service that is perfectly fine.
for i in $(seq 20); do
  curl -fsS --max-time 2 http://127.0.0.1:5000/ >/dev/null 2>&1 && { echo "  serving OK"; break; }
  sleep 1
done

# 11. From here on, deploys are one command.
/opt/arcade-tracker/deploy/deploy.sh --check
```

Two untracked files on the server, `create_admin_fix.py` and
`requirements_server.txt`, are both folded into the repository now
(`scripts/create_admin.py`, and the `requirements.txt` / `requirements-pi.txt`
split — which keeps `segno`, that `requirements_server.txt` had dropped even
though the QR label route imports it). Compare them, then delete them.

## If you already chowned the whole checkout

```bash
# Put the code back under root and leave only the writable paths with the service.
chown -R root:root /opt/arcade-tracker
chown root:arcade-tracker /opt/arcade-tracker/.env && sudo chmod 640 /opt/arcade-tracker/.env
for d in instance uploads backups logs static/maintenance_photos static/profile_pics; do
  install -d -o arcade-tracker -g arcade-tracker -m 755 "/opt/arcade-tracker/$d"
  chown -R arcade-tracker:arcade-tracker "/opt/arcade-tracker/$d"
done

# Confirm git is happy again as root.
git -C /opt/arcade-tracker status --porcelain
```

Only if you would rather keep the checkout owned by the service user, tell git so
explicitly instead — but prefer the above, which also stops the web service being
able to rewrite its own code:

```bash
git config --global --add safe.directory /opt/arcade-tracker
```

## One-time: give every machine its roster slug (Phase 1)

Runs once, after the Phase 1 branch is deployed. It makes `Game.barcode` equal the
roster slug, so a code scanned at the cabinet and a code scanned on the bench mean
the same machine. The reviewed map file is the floor list: it lives outside the
checkout and is never committed.

First get the map into the container. This is two hops, not one: the container has no
sshd, so nothing can `scp` into it, and `pct` exists only on the Proxmox host. Run
this from your workstation, substituting the Proxmox host's address:

```bash
ssh root@<pve-host> 'cat > /root/roster-map.staging.json \
  && pct push 131 /root/roster-map.staging.json /root/roster-map.json --perms 600 \
  && pct exec 131 -- sha256sum /root/roster-map.json \
  && rm -f /root/roster-map.staging.json' < roster-map.json
```

Check the sha256 it prints against the local file (`sha256sum roster-map.json`). The
map rewrites machine identities, so a truncated copy is worth catching here rather
than in the dry run. The staging copy on the Proxmox host is deleted by the same
command; the container's copy is deleted at the end of this section.

Then, on the server:

```bash

# 1. Dry run. Changes nothing. Read the list it prints: every machine whose
#    identifier moves, and every entry it is leaving alone with the reason.
cd /opt/arcade-tracker
set -a; . .env; set +a
/opt/arcade-tracker-venv/bin/python scripts/apply_roster_map.py --map /root/roster-map.json

# 2. A dump, because this rewrites identities. deploy.sh takes one; if you are
#    running this outside a deploy, take one by hand first.
pg_dump --clean --if-exists --no-owner --no-privileges "$DATABASE_URL" \
  | gzip -9 > /var/backups/arcade-tracker/pre-roster-map-$(date -u +%Y%m%dT%H%M%SZ).sql.gz

# 3. Apply. It asks for confirmation; --yes skips the prompt.
/opt/arcade-tracker-venv/bin/python scripts/apply_roster_map.py \
  --map /root/roster-map.json --apply \
  --reprint-out /root/labels-to-reprint.json \
  --base-url "${BASE_URL:-}"
```

It refuses rather than guesses. A machine renamed since the map was made, a barcode
edited by hand, a row deleted, two machines claiming one slug: any of these stops
the whole run with nothing written, and the fix is to regenerate the map against the
current database and review it again.

Then, **in this order**:

1. **Import the machines the roster has and the database does not** — Games →
   Import, upload the same roster JSON. Order matters: mapping first means the
   importer recognises those machines by slug and skips them. Importing first makes
   it report them all as name collisions instead.
2. **Set `BASE_URL` in `.env` first, then reprint the labels in
   `/root/labels-to-reprint.json`.** Each entry carries its `label_page`; open it and
   print. Until a label is reprinted it encodes the old identifier and resolves to
   nothing.

   `BASE_URL` is unset by default, and then the label encodes the host of whatever
   browser printed it. Print from the LAN and the QR is a LAN address that resolves
   to nothing from a phone on cellular -- which is the scan you actually need at a
   cabinet. Set it to the public hostname and restart before printing:

   ```bash
   printf 'BASE_URL=https://<public-hostname>\n' >> /opt/arcade-tracker/.env
   systemctl restart arcade-tracker
   ```

   Check one label page before printing 26: the URL under the QR code must be the
   public one. GATBOX is indifferent -- it reads only the slug and never checks the
   host -- so this is purely about the phone scan.
3. Delete `/root/roster-map.json` and `/root/labels-to-reprint.json` from the
   server once the labels are printed. Both are the floor list.

## If the database is SQL_ASCII

Symptom: a save fails with `'ascii' codec can't encode character '\u2014'`. The roster
import hits it on the twelve entries whose notes contain an em dash, and so would any
maintenance note a person types with one.

Cause: the database was created with encoding `SQL_ASCII`, which happens when it is
created under a `C` locale with no explicit encoding. psycopg2 then maps the connection
to Python's `ascii` codec and refuses every character above 127. `check_schema.py`
reports it, and refuses the deploy when both ends are SQL_ASCII.

```bash
cd /opt/arcade-tracker && set -a; . .env; set +a
psql "$DATABASE_URL" -tAc "SHOW server_encoding"        # SQL_ASCII = this is you
```

### Unblock now (one statement, no downtime, no file edits)

Set the default on the role instead of in the connection string. On the database host,
as the `postgres` superuser:

```bash
psql -c "ALTER ROLE arcade_tracker SET client_encoding TO 'UTF8'"
```

```bash
systemctl restart arcade-tracker          # on the application host
```

Every new connection by that role gets UTF8, and the server stores the bytes verbatim,
so writes and reads round-trip exactly — verified on em dash, degree sign and accented
characters. Undo with `ALTER ROLE arcade_tracker RESET client_encoding`.

**Do not do this by editing `DATABASE_URL` in `.env`.** It works —
`?client_encoding=utf8` on the URL has the same effect — but it means hand-editing the
one line that holds the database password, and a hand-edit that truncates it takes the
site down with `FATAL: password authentication failed` and no way to recover the old
value. That happened on 2026-10-01. The role default achieves the same thing without
going near the credential.

Either way this is a stopgap: it leaves a database that does no encoding validation and
sorts by byte value.

### The fix: convert to UTF8

The dataset is small and the downtime is a minute. Do it before the database grows, and
before GATBOX starts posting maintenance text into it — a technician's em dash would
otherwise fail an ingest.

**This spans two containers.** Renaming and creating a database needs a superuser, and
the application role is not one, so steps 2 and 3 run on the PostgreSQL host as the
`postgres` user. Everything else runs on the application host.

**On the application host** — stop the service and dump:

```bash
cd /opt/arcade-tracker && set -a; . .env; set +a
systemctl stop arcade-tracker
pg_dump --no-owner --no-privileges "$DATABASE_URL" > /root/pre-utf8.sql
wc -c /root/pre-utf8.sql
```

`--no-owner --no-privileges` so it restores cleanly into a fresh database. Check the byte
count: a few hundred bytes means the dump caught nothing and you must stop here.

**On the PostgreSQL host**, as the `postgres` user — rename the old one, create the new:

```bash
psql -c "ALTER DATABASE arcade_tracker RENAME TO arcade_tracker_sqlascii"
```

```bash
psql -c "CREATE DATABASE arcade_tracker ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0 OWNER arcade_tracker"
```

The rename needs no open connections, which is why the service is stopped first. `C`
collation with `UTF8` encoding is correct and needs no locales installed on the database
host — it sorts by byte value. `TEMPLATE template0` is required in order to choose an
encoding at all. `OWNER arcade_tracker` matters: since PostgreSQL 15 the `public` schema
belongs to the database owner, so the application role can restore into it without being
a superuser. Verified against 18.6 with a non-superuser owner.

**Back on the application host** — restore and prove it before starting:

```bash
psql "$DATABASE_URL" -q -f /root/pre-utf8.sql
```

If that errors on an invalid byte sequence, the old database held text in some other
encoding. Stop and work out what it was; do not force it.

```bash
psql "$DATABASE_URL" -tAc "SHOW server_encoding"
psql "$DATABASE_URL" -tAc "SELECT count(*) FROM game"
/opt/arcade-tracker-venv/bin/python scripts/check_schema.py
systemctl start arcade-tracker
```

Expect `UTF8`, the same machine count as before, and no encoding complaint.

Afterwards, undo whichever stopgap was in place — on the PostgreSQL host as `postgres`:

```bash
psql -c "ALTER ROLE arcade_tracker RESET client_encoding"
```

A UTF8 database gives a UTF8 connection by default, so the override is no longer doing
anything and leaving it only hides the next problem.

Drop the old database once you are satisfied, not before — on the PostgreSQL host:

```bash
psql -c "DROP DATABASE arcade_tracker_sqlascii"
```

Rehearsed on a throwaway cluster: an em dash already stored in the SQL_ASCII database
survives the dump and restore byte-for-byte, new non-ASCII inserts work afterwards with
no client override, and a non-superuser database owner can create tables and write
non-ASCII in a database it owns.

## Where a work order came from

Three sources now, shown on the queue and on the order page:

| `source` | what it means |
|---|---|
| `web` (or empty) | typed into this application. Empty means it predates Phase 2. |
| `coindoor` | somebody at the machine, with no login — they opened the coin door and scanned the label inside it. No account behind the description. |
| `gatbox` | raised on GATBOX's 7″ screen at the bench, with the rail trace that prompted it. |

A `gatbox` order carries **Prompted by** — the session that caused it — and may collect
**Also attached** traces measured afterwards. Both link straight to the trace. An order with
neither shows no section at all.

GATBOX pushes these through `POST /api/v1/ingest`, so they arrive on the device token, not a
session. Two things it is worth knowing when one looks wrong:

- **An order for a machine this hub does not have is refused**, by design, with the slug in
  the reason — the hub never invents a `Game`. GATBOX retries it five times and then parks it
  and says so on its own screen. The fix is here: give the machine its roster slug, and the
  next sync takes the order.
- **An order naming a trace this hub has not ingested is also refused**, and that one clears
  by itself: GATBOX sends the session and the order in the same request, so it normally never
  happens, and when it does the retry resolves it.

Device tokens are issued with `scripts/create_device.py --name gatbox-01` and shown once.

## Rollback

```bash
git -C /opt/arcade-tracker reset --hard <previous-sha>
runuser -u arcade-tracker -- bash -c 'set -a; . /opt/arcade-tracker/.env; set +a; \
  gunzip -c /var/backups/arcade-tracker/arcade_tracker-<stamp>.sql.gz | psql "$DATABASE_URL"'
systemctl restart arcade-tracker
```

`deploy.sh` prints the two values to substitute when it fails.

A deploy doesn't touch the uploaded files, so a rollback leaves them alone.

## Restoring photos and uploads

Each deploy's `arcade_tracker-uploads-<stamp>.tar.gz` holds the files as they were when its
dump of the same stamp was taken; restore the pair together after a loss. Paths in the
archive are relative to the checkout, so it unpacks straight into it. Files that are on disk
now but not in the archive are kept (a later cleanup removes any no order uses).

```bash
ls -1t /var/backups/arcade-tracker/arcade_tracker-uploads-*.tar.gz | head -3     # newest first
tar -tzf /var/backups/arcade-tracker/arcade_tracker-uploads-<stamp>.tar.gz | head  # what it holds
systemctl stop arcade-tracker
tar -xzf /var/backups/arcade-tracker/arcade_tracker-uploads-<stamp>.tar.gz -C /opt/arcade-tracker
chown -R arcade-tracker:arcade-tracker /opt/arcade-tracker/uploads \
  /opt/arcade-tracker/static/maintenance_photos /opt/arcade-tracker/static/profile_pics
systemctl start arcade-tracker
```

With the database too (the dump of the same stamp), restore it first, as under "Rollback".

## Reading the logs

Everything goes to the journal, one event per line as `key=value` pairs:

```bash
journalctl -u arcade-tracker -f                                   # follow
journalctl -u arcade-tracker --since today | grep event=LOGIN_FAILED
journalctl -u arcade-tracker | grep request_id=<reference>         # one request, every line
journalctl -u arcade-tracker --since "1 hour ago" | grep -E 'status=5|level=ERROR'
```

An error page shows a **Reference**; that's the `request_id`. Through the tunnel it's
Cloudflare's ray ID, so it also finds the request in the Cloudflare dashboard. Each page request
writes one `event=request` line (method, path, status, `ms`, user, visitor `ip`); static files
don't. `LOG_LEVEL` in `.env` changes the level (default `INFO`; `WARNING` for quieter logs).

## Notes

- **Never `flask db stamp head` on a database you have not checked.** Stamping
  marks migrations as applied *without running them*. A database built by
  `db.create_all()` at some point in the past is stamped at nothing, but its
  schema is only as new as the models were that day — so `stamp head` can claim a
  column exists that does not, `db upgrade` becomes a no-op, and the first page to
  touch that column raises `UndefinedColumn`. That is exactly what happened on
  2026-10-01: the tables were all present, `game.barcode` was not.

      python scripts/check_schema.py     # compares live schema to the models

  It names every missing table and column, says which revision introduced each,
  and prints the `stamp` + `upgrade` pair that repairs it. `deploy.sh` runs it
  after migrating and refuses to restart the service if anything is still missing.
- `deploy.sh`'s liveness gate is `/`, the thinnest path that proves the app is
  serving. It probes `/skeeball/api/health` afterwards and only warns: that route
  reaches the lane manager and so gpiozero, and a dependency problem there should
  not fail a deploy that is otherwise fine.
- `/skeeball/api/health` is the one route with no login. Do not add a session
  requirement to it.
- **Never curl straight after `systemctl restart`.** Type=simple means the restart
  returns as soon as the process is forked; waitress binds a moment later. Wait for
  readiness in a loop, as above. `deploy.sh` already does this (ten attempts, two
  seconds apart).
- **The client must be >= the server.** `deploy.sh --check` verifies it and prints
  the PGDG install commands if not. The server is PostgreSQL 17; Debian bookworm
  ships client 15, which `pg_dump` refuses to use against it.
- `check_schema.py` also checks the **database encoding**. A SQL_ASCII database stores
  no character above 127 — psycopg2 uses Python's `ascii` codec — so one em dash in a
  machine note fails the save. It refuses the deploy when both ends are SQL_ASCII and
  warns when only the client is overridden. See "If the database is SQL_ASCII".
- `check_schema.py` also checks the **identity sequences** on PostgreSQL. One left
  behind its table's largest id serves every page and fails every insert with a
  duplicate key, which is the same shape of fault as the bad stamp: everything
  present, nothing usable. It happens when rows are restored with explicit ids and
  the reset is missed, so it is a restore's characteristic parting gift. The script
  prints the `setval` that repairs each one, and `deploy.sh` will not restart the
  service while any sequence is behind.
- The migrations cannot build a schema from nothing — none of them creates the
  base tables. A brand-new database is made with `create_all` and then
  `flask db stamp head`, not `flask db upgrade`.
