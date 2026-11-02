#!/usr/bin/env bash
# The privacy check: what a range of commits would publish, checked before it leaves this
# machine. The repositories are public, and history keeps whatever is pushed even after a
# delete. Run it by hand before a push; .githooks/pre-push runs it on every push and refuses
# the push when anything is found.
#
# ONE SCRIPT FOR THE THREE REPOSITORIES (GATBOX, cabcrawl, the tech tracker), byte-identical,
# owned by GATBOX and vendored like docs/agents/RULES.md. What differs per repository is in its
# .githooks/privacy.conf, beside this script's checkout: the paths that repository must never
# commit, on top of the shared list below.
#
#   scripts/privacy_check.sh                         # origin's default branch..HEAD (all of HEAD
#                                                    # if there is no origin)
#   scripts/privacy_check.sh <rev-list args...>      # e.g. main..HEAD
#   PRIVACY_CI=1 scripts/privacy_check.sh ...        # CI: no owner patterns exist there, so
#                                                    # section 4 is skipped (and says so)
#
# Every commit in the range is checked on its own (git log -p), not just the net diff: a
# secret added in one commit and deleted in the next is still in the history.
#
# Owner-specific values (names, hostnames, the LAN) live outside the repository, one
# extended regex per line, # comments and blank lines skipped. The file is the first of:
#   $PRIVACY_PATTERNS_FILE (when set, it is the only one tried; so are the older names
#                           $GATBOX_HOOK_PATTERNS, $CABCRAWL_HOOK_PATTERNS, $SHOP_HOOK_PATTERNS)
#   .githooks/patterns.local                (git-ignored)
#   ~/.config/gatbox-family/patterns.local  (a cloud session writes it from $PRIVACY_PATTERNS)
#   ~/.config/shop-hub/patterns.local       (the tech tracker's older place)
# Without one the check fails: a push is never let through unchecked. A match from that
# file is never printed, only where it is, and neither is the pattern. The login name and
# home directory are checked without being written anywhere.
set -uo pipefail

# This script's own checkout, for its .githooks/privacy.conf. The repository checked is the
# one the current directory is in (the same one, except in the tests).
HERE=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd) || exit 2
CONF=$HERE/.githooks/privacy.conf
ROOT=$(git rev-parse --show-toplevel) || exit 2
cd "$ROOT" || exit 2
CI=${PRIVACY_CI:-${SHOP_PRIVACY_CI:-}}

PATTERNS=
for v in PRIVACY_PATTERNS_FILE GATBOX_HOOK_PATTERNS CABCRAWL_HOOK_PATTERNS SHOP_HOOK_PATTERNS; do
  if [ -n "${!v:-}" ]; then PATTERNS=${!v}; PATTERNS_FROM=\$$v; break; fi
done
if [ -z "$PATTERNS" ]; then
  PATTERNS_FROM="any of .githooks/patterns.local, ~/.config/gatbox-family/patterns.local"
  for f in "$ROOT/.githooks/patterns.local" "$HOME/.config/gatbox-family/patterns.local" \
           "$HOME/.config/shop-hub/patterns.local"; do
    if [ -r "$f" ]; then PATTERNS=$f; break; fi
  done
fi

if [ "$#" -gt 0 ]; then
  RANGE=("$@")
else
  RANGE=(HEAD)          # nothing published yet: the whole history is about to be
  for b in origin/HEAD origin/main origin/master; do
    if git rev-parse -q --verify "refs/remotes/$b" >/dev/null; then RANGE=("$b..HEAD"); break; fi
  done
fi

# The only addresses allowed in commits and content. RFC 2606 / 6761 names are reserved and
# can never belong to anyone, so invented data uses them.
readonly EMAIL_OK='^(240414701\+digitalunconciousness@users\.noreply\.github\.com|noreply@anthropic\.com|[^@]+@([a-z0-9-]+\.)*(example\.(com|org|net)|example|test|invalid|localhost))$'

found=0
section() { printf '\n[%s]\n' "$1"; }
clean()   { printf '  clean\n'; }
hit()     { found=1; printf '  %s\n' "$*"; }

mapfile -t COMMITS < <(git rev-list --no-merges "${RANGE[@]}" 2>/dev/null)
printf 'privacy check: %s (%d commit(s))\n' "${RANGE[*]}" "${#COMMITS[@]}"
if [ "${#COMMITS[@]}" -eq 0 ]; then
  printf '  nothing to check\n'
  exit 0
fi

# Added lines as "<sha7>\t<path>\t<line>\t<text>", one commit at a time.
ADDED=$(git log -p -U0 --no-color --no-ext-diff --no-merges --format='commit %h' "${RANGE[@]}" | awk '
  /^commit [0-9a-f]+$/      { sha = $2; next }
  /^\+\+\+ /                { path = substr($0, 5); sub(/^b\//, "", path); next }
  /^@@ /                    { split($3, a, ","); line = substr(a[1], 2) + 0; next }
  /^\+/                     { printf "%s\t%s\t%d\t%s\n", sha, path, line, substr($0, 2); line++; next }
')
MESSAGES=$(git log --no-merges --format='%h%x09%B%x1e' "${RANGE[@]}")

# grep_added LABEL ECHO PATTERN [EXCLUDE]: report added lines matching PATTERN (ERE,
# case-insensitive). ECHO=1 shows the match; 0 shows only where it is.
grep_added() {
  local label=$1 show=$2 pat=$3 excl=${4:-} out
  out=$(printf '%s\n' "$ADDED" | awk -F'\t' -v OFS='\t' '{ t=$4; for (i=5;i<=NF;i++) t=t FS $i; print $1, $2, $3, t }' \
        | while IFS=$'\t' read -r sha path line text; do
            m=$(printf '%s\n' "$text" | grep -oiE -- "$pat") || continue
            if [ -n "$excl" ]; then
              m=$(printf '%s\n' "$m" | grep -viE -- "$excl") || continue
            fi
            if [ "$show" = 1 ]; then
              printf '%s:%s (%s): %s\n' "$path" "$line" "$sha" "$(printf '%s' "$m" | paste -sd' ')"
            else
              printf '%s:%s (%s): [match not shown]\n' "$path" "$line" "$sha"
            fi
          done)
  section "$label"
  if [ -n "$out" ]; then while IFS= read -r l; do hit "$l"; done <<<"$out"; else clean; fi
}

# --- 1. commit identity and time ------------------------------------------------------------
section "commit author/committer: noreply email, UTC timestamps"
bad_meta=0
for c in "${COMMITS[@]}"; do
  read -r ae ce < <(git show -s --format='%ae %ce' "$c")
  for e in "$ae" "$ce"; do
    printf '%s\n' "$e" | grep -qE "$EMAIL_OK" || { hit "$(git rev-parse --short "$c"): email $e"; bad_meta=1; }
  done
  for d in $(git show -s --format='%ad%n%cd' --date=iso-strict "$c"); do
    case "$d" in *+00:00|*Z) ;; *) hit "$(git rev-parse --short "$c"): non-UTC time $d (commit with TZ=UTC)"; bad_meta=1 ;; esac
  done
done
[ "$bad_meta" = 0 ] && clean

# --- 2. paths that must never be committed --------------------------------------------------
# Shared: secrets, keys, databases and dumps, the owner's local files (*.local*), manuals
# (PDF) and ROM images. Then this repository's own list from .githooks/privacy.conf: its
# data, uploads and photos.
FORBIDDEN_PATHS='(^|/)\.env(\.|$)|\.(sql|dump|db|sqlite[0-9]*|db-(journal|wal|shm)|pem|key|p12|pfx|psk|token|pdf|bin|rom)$|\.local|(^|/)id_(rsa|ed25519|ecdsa)'
extra=0
ALLOWED_ADDRESSES=
if [ -r "$CONF" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      forbid=?*) FORBIDDEN_PATHS="$FORBIDDEN_PATHS|${line#forbid=}"; extra=$((extra + 1)) ;;
      allow_address=?*) ALLOWED_ADDRESSES="${ALLOWED_ADDRESSES:+$ALLOWED_ADDRESSES|}${line#allow_address=}" ;;
    esac
  done < "$CONF"
fi
section "forbidden paths (.env, databases, dumps, keys, *.local*, *.pdf, *.bin, and $extra of this repository's own)"
paths=$(git log --no-merges --format= --name-only --diff-filter=ACMR "${RANGE[@]}" | sort -u \
        | grep -iE -- "$FORBIDDEN_PATHS" | grep -vE '(^|/)(\.env\.example|patterns\.local\.example)$')
if [ -n "$paths" ]; then while IFS= read -r p; do hit "$p"; done <<<"$paths"; else clean; fi

# --- 3. added content -----------------------------------------------------------------------
# A documented default this repository may name (.githooks/privacy.conf allow_address=) is
# left out; anything else private is site data.
grep_added "private IP addresses (RFC 1918, CGNAT, IPv6 ULA)" 1 \
  '(^|[^0-9.])(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}|f[cd][0-9a-f]{2}:[0-9a-f:]+)([^0-9]|$)' \
  "${ALLOWED_ADDRESSES:-}"

emails=$(printf '%s\n' "$ADDED" | while IFS=$'\t' read -r sha path line text; do
           printf '%s\n' "$text" | grep -oiE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
             | grep -viE "$EMAIL_OK" | sed "s|^|$path:$line ($sha): |"
         done)
section "email addresses (allowed: the noreply ones and reserved example/test domains)"
if [ -n "$emails" ]; then while IFS= read -r l; do hit "$l"; done <<<"$emails"; else clean; fi

# The login name identifies the owner only on the owner's machine. In CI (runner) or a cloud
# session (root) it's a generic word that appears in legitimate content, so there only the
# generic home-path patterns are checked.
me=$(id -un)
if [ -n "$CI" ] || [ "$me" = root ]; then
  grep_added "home paths (login name not checked: CI or root)" 1 \
    "(/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+)"
else
  grep_added "login name and home paths" 1 \
    "(/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+|${HOME//./\\.}|(^|[^A-Za-z0-9])${me}([^A-Za-z0-9]|$))"
fi

# shellcheck disable=SC2016  # a $ in these patterns is a regex character, not a variable
grep_added "token- and key-shaped strings" 0 \
  'gbx_[0-9a-f]{12}\.[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16}|sk-(ant-)?[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|tk_[A-Za-z0-9]{29}|discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{30,}\.?|postgres(ql)?(\+psycopg[0-9]?)?://[^:/@[:space:]]+:[^<${@[:space:]][^@[:space:]]*@|(secret[_-]?key|api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|passphrase|psk|totp[_-]?secret)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'[:space:]]{8,}["'"'"']' \
  '(test|example|dummy|fake|changeme|not-a-real|synthetic|placeholder|your-)'

# A literal is a secret; an expression is code. A Wi-Fi key or a password written bare, to the
# end of the line or a comment (a keyfile, a note: psk=..., password: ...). A value with a
# call, an attribute, a comma or a variable in it is code, and so is "password = x", which
# is how Python assigns one, so neither is matched.
grep_added "bare Wi-Fi keys and passwords" 0 \
  '(psk|passphrase)[[:space:]]*[:=][[:space:]]*[^"'"'"'[:space:](),.$]{6,}[[:space:]]*(#.*)?$|(password|passwd)([[:space:]]*:[[:space:]]*|=)[^"'"'"'[:space:](),.$]{6,}[[:space:]]*(#.*)?$|wpa[_-]?passphrase' \
  '(test|example|dummy|fake|changeme|not-a-real|synthetic|placeholder|your-)'

# --- 4. owner-specific patterns -------------------------------------------------------------
section "owner patterns (never shown, only where they match)"
if [ -n "$CI" ]; then
  printf '  skipped (CI: owner patterns are local-only; the pre-push hook checks them)\n'
elif [ -n "$PATTERNS" ] && [ -r "$PATTERNS" ]; then
  n=0; lhits=0
  while IFS= read -r pat || [ -n "$pat" ]; do
    case "$pat" in ''|\#*) continue ;; esac
    n=$((n + 1))
    printf '' | grep -qE -- "$pat" 2>/dev/null
    if [ "$?" -eq 2 ]; then hit "pattern #$n is not a valid extended regex: fix the patterns file"; lhits=1; continue; fi
    where=$(printf '%s\n' "$ADDED" | while IFS=$'\t' read -r sha path line text; do
              printf '%s\n' "$text" | grep -qE -- "$pat" 2>/dev/null && printf '%s:%s (%s)\n' "$path" "$line" "$sha"
            done)
    paths_hit=$(git log --no-merges --format= --name-only "${RANGE[@]}" | sort -u | grep -cE -- "$pat" 2>/dev/null)
    msg=$(printf '%s' "$MESSAGES" | grep -cE -- "$pat" 2>/dev/null)
    [ -n "$where" ] && { while IFS= read -r w; do hit "pattern #$n in $w"; done <<<"$where"; lhits=1; }
    [ "${paths_hit:-0}" -gt 0 ] && { hit "pattern #$n in a file name"; lhits=1; }
    [ "${msg:-0}" -gt 0 ] && { hit "pattern #$n in a commit message"; lhits=1; }
  done < "$PATTERNS"
  printf '  (%d pattern(s) loaded)\n' "$n"
  if [ "$n" -eq 0 ]; then
    hit "EMPTY: the owner patterns file has no patterns in it. Add them before pushing."
  elif [ "$lhits" = 0 ]; then
    clean
  fi
else
  hit "MISSING: no owner patterns file (looked for $PATTERNS_FROM)."
  hit "Copy .githooks/patterns.local.example to ~/.config/gatbox-family/patterns.local and fill"
  hit "it in. NOTHING MAY BE PUSHED UNTIL IT EXISTS."
fi

# --- 5. commit messages ---------------------------------------------------------------------
section "commit messages (IPs, emails, home paths)"
mhits=$(printf '%s' "$MESSAGES" | tr '\036' '\n' \
        | grep -oiE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(10|192\.168)\.[0-9]{1,3}\.[0-9]{1,3}(\.[0-9]{1,3})?|/home/[A-Za-z0-9._-]+' \
        | grep -viE "$EMAIL_OK" | { if [ -n "$ALLOWED_ADDRESSES" ]; then grep -vE -- "$ALLOWED_ADDRESSES"; else cat; fi; })
if [ -n "$mhits" ]; then while IFS= read -r l; do hit "$l"; done <<<"$mhits"; else clean; fi

printf '\n'
if [ "$found" -ne 0 ]; then
  printf 'privacy check: FOUND SOMETHING (see above)\n'
  exit 1
fi
printf 'privacy check: clean\n'
exit 0
