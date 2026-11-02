# Working on Cabcrawl

Cabcrawl runs an arcade floor: machines, work orders, parts, coin meters, and the rail readings
GATBOX sends. It's a Flask app, deployed as a systemd service behind a Cloudflare tunnel, with
PostgreSQL in production and SQLite for development and tests. The user guide is in `docs/wiki/`
(published to the GitHub wiki), and the server runbook is in `deploy/RUNBOOK.md`.

**Read `docs/agents/RULES.md` first.** It is the one set of agent rules for GATBOX, cabcrawl and
the tech tracker (GATBOX owns it; this copy is vendored, byte-identical), and it wins where this
file disagrees, except on this repository's own facts and the hard rules below.

## Hard rules

- **No personal data in the repository.** The repository is public. Don't commit usernames,
  home paths, LAN addresses, real machine rosters or names, tokens, databases or logs. Tests,
  screenshots and docs use invented names; `tests/characterization/seed.py` builds a synthetic
  arcade for them.
- **Printed labels never change.** QR labels on cabinets and shelves encode `/g/<barcode>`,
  `/p/<code>`, `/i/<code>`, `/u/<code>` and `/report/<token>`. Never change those routes or the
  stored codes (`Game.barcode` and the others). Any other URL change needs a redirect from the
  old address.
- **The GATBOX API contract is frozen.** `/api/v1` and `contract/v1/` change only on purpose.
  GATBOX owns the contract (since 2026-10-28) and this copy is vendored from its
  `docs/contract/v1/`, byte-identical; `tests/test_api_v1_contract.py` and the checksums enforce
  it, and compare it with any sibling checkout. The skeeball API is left exactly as it is.
- **Never touch the production database** or `instance/arcade.db`. Work against a scratch
  database.
- **Commits use UTC** (`TZ=UTC git commit ...`).
- **Old names stay on the server.** The app was called Arcade Tracker until October 2026. The
  systemd unit `arcade-tracker.service`, `/opt/arcade-tracker`, the database and role, cookie
  names and environment variable names keep that name on purpose.

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                                   # the whole suite, on SQLite
python -m pytest tests/test_inventory_links.py     # one file
ruff check <files you changed>                     # no new findings in what you touch
python scripts/check_schema.py                     # the database matches the models
scripts/privacy_check.sh master..HEAD              # what a push would publish (clean before any push)
```

- **Screenshots** of every page, at 390 and 1280 wide, on the synthetic arcade:
  `python tests/screenshots/take.py <outdir>`. `tests/screenshots/csp_check.py` loads every
  page in a browser and fails on any content-security-policy violation. Both need Playwright;
  set `PLAYWRIGHT_CHROMIUM` to use an installed Chromium.
- **The characterization suite** (`tests/characterization/`) pins what every page shows and
  accepts, per role. When a change is meant to alter what a role sees, regenerate the snapshot
  with `UPDATE_SNAPSHOTS=1 python -m pytest tests/characterization/test_visibility.py` and
  check the diff loses nothing by accident.
- **PostgreSQL behaves differently** (foreign keys enforced, row locks, stricter types). Run the
  affected tests against a throwaway PostgreSQL too, by pointing `DATABASE_URL` at it in
  `tests/conftest.py` for the run.

## How the code is put together

- `app/routes/` holds one blueprint per area. Routes stay thin: the work is in `app/services/`.
- **Every stock change goes through `app/services/stock_places.py`** (`add`, `take`, `count`,
  `move`, `set_total`). It keeps the per-place levels and the part's total in step, and it locks
  the part's row on PostgreSQL.
- **Pages extend `templates/layout.html`** and are built from the macros in `templates/ui.html`
  (`page_head`, `field`, `readout`, `chip`, `empty`, `danger_form`...). Every component is shown
  at `/styleguide`. `docs/REDESIGN.md` explains the design system and how to add a page.
- **No inline scripts or handlers.** The content security policy allows only `script-src 'self'`.
  Behaviour goes in `static/js/` and hooks onto `data-` attributes.
- **Roles** are readonly < operator < manager < admin. Guard every route with `@login_required`
  and `@requires_role(...)`, and offer an action in a page only to the roles its route allows.
- **Migrations**: add an Alembic migration that upgrades and downgrades cleanly, and register it
  in `scripts/check_schema.py` (`INTRODUCED_BY`, `TABLE_INTRODUCED_BY`, `PREDECESSOR`, `ORDER`).
  A new database is built with `create_all` and then `flask db stamp head`.
- Known bugs and decisions are numbered F-1, F-2... in `docs/FEATURES.md`.

## How changes are made

- Small steps and small commits, each with a test that fails without the change.
- **The privacy check guards every push.** Enable it once per clone with `git config core.hooksPath
  .githooks`; it needs the owner's patterns file (`.githooks/patterns.local`, git-ignored, or
  `~/.config/gatbox-family/patterns.local`) and refuses the push without one. Never print or
  copy that file's contents. The script and hook are the shared ones, the same in all three
  repositories; what only this one must never commit is in `.githooks/privacy.conf`.
- One OK per change (the rules, §3): the owner's OK on the design note covers the code and the
  push. The PR shows the diff stat, the test results, the privacy-check output and screenshots
  (390 and 1280); the owner merges and deploys.
- Ask rather than guess when a decision belongs to the owner.
- Say plainly what was and wasn't checked.
- Use invented data in every test, screenshot and doc.
