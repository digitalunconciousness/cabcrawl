# Rules for agents, in all three repositories

This file is the same in GATBOX, cabcrawl and ArcadeTech-Tracker. GATBOX owns it
(`docs/agents/RULES.md`) and the other two vendor it byte-identical, with `CHECKSUMS` beside it
and a test in every repository that also compares it with any sibling checkout. Change it only
in GATBOX, then copy it to the other two in the same change.

**Read this first, then the repository's `CLAUDE.md`.** This file wins where they disagree,
except on that repository's own facts: its stack, commands, layout and its own hard rules.

Decided by the owner, 2026-10-28: one shared set of rules, and one OK per change (§3).

---

## 1. The system

Three repositories, all **public**:

| repository | what it is | its world |
|---|---|---|
| GATBOX | the deck: a Pi 5 bench and diagnostic box (meter, T48, 7" touch screen, scanner), offline-first; and, from phase D of the integration plan, the archive that keeps the owner's game knowledge in a container of the owner's own | neither: these are the owner's tools. The deck talks to each world separately; the archive talks to neither |
| cabcrawl | the floor app for the barcade where the owner works: machines, plays, rotation, maintenance, inventory | the **barcade** |
| ArcadeTech-Tracker (deployed as shop-hub) | the side business: customers, assets, jobs, parts, money | the **side work** |

**The two worlds never talk.** No code, request, file or record goes from cabcrawl to the tech
tracker or back, directly or through the deck. The deck keeps each world's data, outbox, token
and state apart, and a last-line guard in `gatbox-sync` refuses anything that would cross. A
world's name and data never appear in the other world's repository.

The plan these rules serve: `docs/superpowers/specs/2026-10-28-gatbox-integration-plan.md` in
GATBOX.

## 2. Hard rules (every repository)

1. **Nothing personal or site-specific in git:** no names, usernames, emails (only the noreply
   addresses and reserved domains), home paths, hostnames, LAN addresses, Wi-Fi names,
   timezones, workplace or customer data, real rosters, databases, dumps or logs.
2. **Nothing copyrighted or secret in git:** no manuals (PDF), ROM images or dumps, tokens,
   keys, `.env` files or uploaded files.
3. **Test data is invented:** Widget Wars, `555-01xx` phone numbers, `example.com` and `.test`.
4. **Commit** as `digitalunconciousness <240414701+digitalunconciousness@users.noreply.github.com>`
   with `TZ=UTC git commit …`.
5. **The privacy check runs before every push and blocks it on a finding** (§6). Never push
   without the owner's patterns file present.
6. **Agents never change a host:** not the Pi, an LXC, the Postgres host, Cloudflare or GitHub
   settings. Read-only checks on the machine the session runs on are fine (journalctl, lsusb,
   `minipro -L`). A change to a host is written as exact commands marked ⏸, and then the agent
   stops.
7. **Never merge, never deploy, never push to `main` or `master`,** and never force-push a
   branch someone else made.
8. **Don't guess:** hardware facts are read off the device or asked for, spec numbers come only
   from a manual the owner cites, and a decision that belongs to the owner is asked for, not
   assumed.
9. **Backward compatible:** printed labels, existing URLs, stored files and data formats keep
   working. Each repository's `CLAUDE.md` lists its own frozen items.
10. **Problem reports stay private.** A report filed in one of the apps can name a customer, a
    machine or the barcade. Turning one into a GitHub issue means stripping the reporter, names,
    ids and business data first, and showing the owner the text before anything is posted.

## 3. How a change flows

1. **A design note first** for anything beyond a small fix: what changes, data, routes, risks,
   tests. One note may cover a change that spans several repositories.
2. **The owner's OK on the note is the one OK the change needs.** It covers the code, the tests
   and the push. No separate go per step or per push.
3. **Code and tests in small commits,** each with a test that fails without it, using the
   repository's own test commands (its `CLAUDE.md`).
4. **Push to a branch once the tests and the privacy check pass,** and open (or update) one PR
   per repository per change. The PR shows the diff stat, the test results and the
   privacy-check output, clean or not. UI changes also get screenshots (cabcrawl: 390 and 1280
   wide; the deck: the 7" layout and a phone; the tech tracker: desktop and phone).
5. **The owner reviews, merges and deploys.** An agent never does.
6. **Say plainly** what was and wasn't checked.

## 4. Working across repositories

- A session may change any of the three repositories when the task needs it, each through its
  own branch and PR. (This replaces "the reference repositories are read-only".)
- **Port patterns; never import code** from another repository.
- **Shared files are byte-identical in every repository that carries them, and GATBOX owns
  them:**
  - contract v1: `docs/contract/v1/` in GATBOX, `contract/v1/` in the hubs, with `CHECKSUMS`;
  - these rules: `docs/agents/RULES.md`, with `CHECKSUMS`;
  - the privacy check and hook: `scripts/privacy_check.sh` and `.githooks/pre-push`;
  - from phase A2, the themes: `design/`, with `CHECKSUMS`.

  A change to one lands in all the PRs together, and each repository's tests compare its copy
  with any sibling checkout they find (beside it, or a cloud session's `/opt/refs/`).
- A world's contract extension (`contract/floor-v1/`, `contract/shop-v1/`) is vendored only
  into GATBOX and that world's app.

## 5. Shared design

- **One philosophy, four themes:** Synthwave, Ledger, Graphite and Follow system, per user in
  the two apps; **the deck stays Synthwave**. The spec is
  `docs/superpowers/specs/2026-10-28-one-look-four-themes.md` in GATBOX, until `design/themes.css`
  and its contrast test land.
- **Components use the roles, never a raw colour:** surface, text, accent, ok / warn / fault /
  info, and so on.
- **Colour means status, and every status has a word.** Glow and scanlines are Synthwave only.
  Numbers, tags, slugs and hashes are set in monospace.
- **Touch targets:** at least 44 px on phones and 56 px on the 7".
- **Printed labels and customer documents stay print-first.**
- **Each app serves its own fonts.** No CDN; plain HTML, CSS and JS, no framework, no build step.

## 6. The privacy check

- **One script, `scripts/privacy_check.sh`, and one hook, `.githooks/pre-push`, identical in all
  three repositories.** Enable the hook once per clone with `git config core.hooksPath .githooks`.
  The check runs over each commit in the range: author and committer emails, UTC timestamps,
  forbidden paths (PDFs, ROM images, databases, `.env`, keys, `*.local*`), private addresses,
  emails, home paths and the login name, key shapes and bare keys, and the owner's own patterns.
- **What one repository must never commit, on top of that, is in its `.githooks/privacy.conf`**
  (`forbid=` lines; `allow_address=` for a documented default such as the deck's hotspot).
- **The owner's patterns live outside git:** `~/.config/gatbox-family/patterns.local` (or the
  git-ignored `.githooks/patterns.local`), one extended regex per line. A cloud session's setup
  script writes the first from the environment variable `PRIVACY_PATTERNS`. Missing or empty:
  the check says so and the push is refused. **Never print or copy the patterns' values;** the
  check reports only where a pattern matched.

## 7. Talking to the owner

The owner is a professional arcade tech, fluent in Linux, systemd, Postgres and git. Be direct,
flag trade-offs, skip preamble, keep narration short, and show command output when it matters.
