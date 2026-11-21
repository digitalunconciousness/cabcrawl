# Fonts

Served from this directory, never from a CDN, so the app works offline and leaks no
requests. Every file came from the same pinned `google/fonts` commit GATBOX uses
(`23e54b51ddffbc7713c583748e3bd86f62b1fa4a`), and the source files' sha256 sums match
GATBOX's bootstrap.

| File | Font | Licence | What was done to it |
|------|------|---------|---------------------|
| `chakra-petch-600-latin.woff2` | Chakra Petch SemiBold (display) | SIL OFL 1.1, `OFL-ChakraPetch.txt` | Subset to Latin + punctuation, arrows and the euro sign, then converted to WOFF2 with fontTools `pyftsubset` (78.6 KB TTF to 9.6 KB). Chakra Petch declares **no Reserved Font Name**, so a modified version may keep the name. |
| `ShareTechMono-Regular.ttf` | Share Tech Mono (body) | SIL OFL 1.1, `OFL-ShareTechMono.txt` | **Nothing.** Byte-identical to upstream (sha256 `9ceab1f8…a6732677`). The font reserves the name "Share", and the OFL forbids a *Modified Version* from using a Reserved Font Name. Whether a WOFF2 conversion or a subset counts as modification is a question for the OFL FAQ, which could not be checked when this was vendored (2026-10-08). Shipping the original file needs no interpretation, and at 43 KB it costs little. |

| `IBMPlexSans-Regular-Latin1.woff2`, `IBMPlexSans-SemiBold-Latin1.woff2` | IBM Plex Sans 400 and 600 (Ledger and Graphite, 2026-11-21) | SIL OFL 1.1, `OFL-IBMPlex.txt` | **Nothing.** IBM's own Latin-1 subsets, byte-identical to `fonts/split/woff2/` in IBM's `@ibm/plex-sans` 1.1.0 on npm (published 2024-11-11). |
| `IBMPlexMono-Regular-Latin1.woff2` | IBM Plex Mono 400 (numbers and codes in Ledger and Graphite) | SIL OFL 1.1, `OFL-IBMPlex.txt` | **Nothing.** The same, from IBM's `@ibm/plex-mono` 2.5.0 (published 2026-06-11). |

Plex reserves the name "Plex", so it is shipped exactly as IBM publishes it, like Share Tech Mono: IBM's own subsets, not
ones made here. Browsers fetch them only on a desktop page in Ledger or Graphite.

The OFL texts must stay beside the fonts: the licence requires that it travels with them.

To rebuild the Chakra Petch subset:

    pyftsubset ChakraPetch-SemiBold.ttf --flavor=woff2 --layout-features='*' \
      --unicodes="U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2190-2199,U+2212,U+2215,U+FEFF,U+FFFD" \
      --output-file=chakra-petch-600-latin.woff2

The pre-redesign fonts (Orbitron and a WOFF2 Share Tech Mono, with their shared `OFL.txt`) were
deleted with `cyberpunk.css` in Step 4.8d. `tests/test_no_external_assets.py` checks that every
file here is one `static/css/base.css` declares.
