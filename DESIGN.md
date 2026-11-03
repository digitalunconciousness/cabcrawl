# Cabcrawl: design direction

The owner's direction for the app's screens, checked at 390 px and 1280 px wide. Agents read it, with antislop's UI, copy and
accessibility skills, as the direction to build toward. It was written from the owner's approved
design notes (`docs/REDESIGN.md`, RULES.md §5 and the four-themes decision of 2026-10-28), not invented.

## Identity

Cabcrawl runs an arcade floor: machines, work orders, parts, coin meters and the rail readings the
bench deck sends. Its look comes from that deck: a dark purple ground, magenta for the one thing to
do, cyan for links, and green, amber and red only for status.

## Personality

- The reading comes first: one big number, its state as a word beside it, and the action next to it.
- Colour means status and only status. A state always has a word, never colour alone.
- Quiet by default. Glow and scanlines belong to Synthwave alone, and even there only on the brand,
  the active tab and status edges.
- Anything copied or compared (tags, barcodes, hashes, readings) is monospace.
- Printed labels (`/g/`, `/p/`, `/i/`, `/u/`, `/report/`) never change their addresses.

## Themes

Four, chosen per user, at every width (decided 2026-10-28, being built): **Synthwave** (the default),
**Ledger** (light, professional), **Graphite** (dark, professional) and **Follow system** (Ledger or
Graphite, as the device says). Components use roles (`--surface`, `--text`, `--accent`, `--ok`,
`--warn`, `--fault`, `--info`), never a raw colour.

- **Synthwave is deliberate.** The deep violet gradient, the neon palette (magenta
  `#ff2e9f`, cyan `#7dfaff`, lime `#5ef2b0`, amber `#ffb74d`, red `#ff4d6d` on bg `#150a28`),
  scanlines and glow on status edges are the owner's chosen identity, shared with the deck, not
  defaults. Chakra Petch for display, Share Tech Mono for the body and numbers.
- **Ledger and Graphite are not exempt.** antislop applies to them in full.
  Both use IBM Plex Sans, with IBM Plex Mono for numbers, and neither has glow or scanlines.

## Mood and use

At least 16 px body text and 44 px touch targets on a phone. Plain HTML, CSS and JS served by the app:
no framework, no build step, no CDN, no inline scripts or handlers.
