#!/bin/bash
# Row 5b: a PR that changes the baseline, known_failures.json or the item set needs the baseline-change
# label, which only the user can add: the session has no GitHub credentials (decision 0006).
#
# Reads git and the labels, and runs nothing from the PR but this file: its Python runs with -I, because
# `python3 -c` otherwise imports a `json.py` from the working directory. CI cannot see who added a label,
# so `ship` checks that (decision 0008). Residual, accepted by the user: a commit could edit this file or
# the workflow, and `ci-change` plus `ship`'s full diff cover that.
#
# Inputs come through the environment, never interpolated into a `run:` line: BASE_SHA, HEAD_SHA, and
# PR_LABELS as a JSON list of names. Any failure here fails the job.
set -euo pipefail

: "${BASE_SHA:?}" "${HEAD_SHA:?}" "${PR_LABELS:?}"

work=$(mktemp -d "${TMPDIR:-/tmp}/baseline-label.XXXXXX")
trap 'rm -rf "$work"' EXIT

# More than one base refuses, as `ship` does: one of them could be where the file looks unchanged.
bases=$(git merge-base --all "$BASE_SHA" "$HEAD_SHA")
[[ "$bases" =~ ^[0-9a-f]{40}$ ]] || {
	echo "baseline-label: FAILED: no single merge base with the base branch" >&2
	exit 1
}
base=$bases
# --no-renames: a rename out of evals/baselines must count as a change there.
git --no-pager diff --text --no-ext-diff --no-textconv --no-renames --name-only -z "$base" "$HEAD_SHA" \
	>"$work/paths"

hit=0
shopt -s nocasematch
while IFS= read -r -d '' path; do
	case "$path" in
	evals/baselines | evals/baselines/* | evals/fixtures/known_failures.json | evals/fixtures/starter_set.jsonl)
		hit=1
		printf 'protected path changed: %s\n' "${path//$'\n'/\\n}" | cat -v
		;;
	esac
done <"$work/paths"
shopt -u nocasematch

if ((hit == 0)); then
	echo "baseline-label: no protected path changed"
	exit 0
fi

# Parsed, not grepped, and only a list counts: `in` on a string would match a substring. An exact,
# case-sensitive name, so a lookalike label does not count.
if python3 -I -c '
import json, os, sys
labels = json.loads(os.environ["PR_LABELS"])
sys.exit(not (isinstance(labels, list) and "baseline-change" in labels))
'; then
	echo "baseline-label: the baseline-change label is present"
	exit 0
fi
echo "baseline-label: FAILED: this PR changes a protected path without the baseline-change label" >&2
exit 1
