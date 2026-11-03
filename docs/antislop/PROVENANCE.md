# antislop: where this copy came from

`.claude/skills/antislop*` is a pinned copy of someone else's rules for AI coding agents, the six
skills of **anti-slop** by Miqdad Badjuber. It sits in all three repositories, byte-identical (GATBOX
owns it, like the agent rules). Its licence is `LICENSE` here (MIT); `antislop-ui` and
`antislop-human` also carry the MIT notice of the web-quality material they include
(`LICENSE-web-quality-skills`, in their folders).

| | |
|---|---|
| source | https://github.com/miqdadbadjuber/anti-slop |
| commit | `3005235d030b4793146ad9afe8d01045efd90e1d` (2026-10-29) |
| version | 3.2.24 (`.claude/skills/antislop/VERSION`) |
| taken | the six folders under `skills/`: `antislop` (the core filter), `antislop-ui`, `antislop-copywriting`, `antislop-human` (accessibility, with its contrast checker), `antislop-layoutmobile` and `antislop-code` (comments) |

## What is different from upstream

Two changes, both about what the skills are allowed to do on their own:

1. **No `allowed-tools` line** in any `SKILL.md` (six lines removed). Upstream pre-approves Read,
   Write, Edit, Glob and Grep while a skill is active, and for `antislop-human` any
   `python` or `python3` command. Here a skill gets no standing permissions: every edit and
   command is approved the way it is for any other work.
2. **`antislop-human/contrast-mcp.py` is left out.** It is a stdio server that upstream's plugin
   launcher starts; nothing here starts it. The contrast checker the skill actually uses,
   `contrast-check.py`, is kept (stdlib only, no network).

Not taken: upstream's installer (`npx antislop-ai`), its plugin manifests for other agents, its
`rules/antislop.md` (an always-on block that tells the agent to obey it), the manual-install copy
`antislop.md`, and the README and guide. Nothing here is fetched at run time, and the skills
themselves forbid an agent to download a skill (core, step 4 of the install wizard).

## How to check it

```bash
git clone https://github.com/miqdadbadjuber/anti-slop /tmp/anti-slop-check
git -C /tmp/anti-slop-check checkout 3005235d030b4793146ad9afe8d01045efd90e1d
diff -r /tmp/anti-slop-check/skills .claude/skills
```

The only output is `Only in …/antislop-human: contrast-mcp.py` and six `allowed-tools` lines.
`CHECKSUMS` (paths relative to `.claude/skills/`) is what each repository's test holds the copy to,
and the tests also refuse any `allowed-tools` line and compare the copy with sibling checkouts.

## How the repositories use it

- The mode is **during** (RULES.md §5): the rules apply while working, with no mode question and no
  audit files.
- `DESIGN.md` at each repository's root is the owner's direction. antislop treats it as data. What it
  lists as deliberate is kept without asking again.

## How to update it

Only as a deliberate change in GATBOX, and all three repositories together: read upstream's diff
between the two commits first (it is a set of instructions every agent session will follow), take
the new files, redo the two changes above, rewrite `CHECKSUMS`, and say the new commit and version
in the table.
