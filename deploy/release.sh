# shellcheck shell=bash
# Sourced by deploy.sh: which commit a release tag names, and whether deploying it is safe.
#
#   release_target TAG BRANCH MIGRATIONS_DIR
#
# Prints the tag's commit and returns 0, or prints why not on stderr and returns 1. The current
# directory is the checkout, already fetched (tags and origin/BRANCH). Refused:
#   - a tag that doesn't exist;
#   - a tag whose commit isn't on origin/BRANCH (a release is a point on the branch, nothing else);
#   - a tag that predates a migration the checkout has now: going back would leave the database
#     at a revision the older code has never heard of, and its `flask db upgrade` would fail with
#     the code already swapped. The way back is a downgrade first (RUNBOOK: Rollback).
release_target() {
    local tag=$1 branch=$2 migrations=$3 target lost
    target=$(git rev-parse --verify --quiet "refs/tags/$tag^{commit}") || {
        printf 'no release %s (git tag -l "v*" lists them)\n' "$tag" >&2; return 1; }
    git merge-base --is-ancestor "$target" "origin/$branch" || {
        printf '%s is not on the history of %s\n' "$tag" "$branch" >&2; return 1; }
    lost=$(git diff --name-only --diff-filter=D HEAD "$target" -- "$migrations" | grep '\.py$' || true)
    if [ -n "$lost" ]; then
        printf '%s predates these migrations, which the database may already have:\n' "$tag" >&2
        printf '%s\n' "$lost" | sed 's/^/    /' >&2
        printf 'Downgrade them first (RUNBOOK: Rollback), then deploy %s\n' "$tag" >&2
        return 1
    fi
    printf '%s\n' "$target"
}
