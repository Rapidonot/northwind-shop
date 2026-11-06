#!/bin/bash
# Row 5b (AUTO-1 finding 3): replace the PR's evals/src with main's, so the gate and integrity code that
# judge a PR are not the PR's. One changed line in cli.py could otherwise repoint the gate and stay green.
#
# Main is fetched here on purpose: ref refs/heads/main, into refs/remotes/origin/main, at full depth. Full,
# because `integrity` needs the merge base with main, so a shallow clone is refused. There is no fallback.
# `set -e` turns any failed fetch, restore or check into a red job: a job that kept going would run the
# PR's own copy and report it as main's.
#
# evals/src is removed first, so a file the PR adds there cannot stay behind beside main's copy.
#
# Residuals (decision 0009): this file and the workflow are PR-editable, and `ci-change` plus `ship`'s full
# diff cover that. The PR's services run in the same process as main's gate, so this raises the cost of
# tampering and is not a boundary.
set -euo pipefail

[[ "$(git rev-parse --is-shallow-repository)" == false ]] || {
	echo "use-main-code: FAILED: this clone is shallow: check out with fetch-depth 0" >&2
	exit 1
}
git fetch --no-tags origin +refs/heads/main:refs/remotes/origin/main
main_sha=$(git rev-parse --verify 'refs/remotes/origin/main^{commit}')

rm -rf evals/src
git restore --source="$main_sha" --worktree -- evals/src

# What is on disk must be exactly main's: every file, by content, and nothing more. Compared on disk,
# because `git diff <commit>` walks the index, where the PR's own adds and deletes still sit.
# -z, because git otherwise quotes an unusual name and it would never match what is on disk.
expected=$(git ls-tree -r -z "$main_sha" -- evals/src | tr '\0' '\n' | cut -d' ' -f3- | LC_ALL=C sort -k2)
found=$(find evals/src -type f | while IFS= read -r file; do
	printf '%s\t%s\n' "$(git hash-object -- "$file")" "$file"
done | LC_ALL=C sort -k2)
[[ -n "$expected" && "$expected" == "$found" ]] || {
	echo "use-main-code: FAILED: evals/src on disk is not main's" >&2
	exit 1
}

echo "use-main-code: evals/src is main's copy, at $main_sha"
