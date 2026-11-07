#!/bin/bash
# AUTO-2 (decision 0011): close a row with one command. `make finish` calls this from the runner clone,
# because it runs with the user's GitHub login. It reaches the clone only through runner-sync, so it is in
# RUNNER_SYNC_PATHS itself. Written for macOS bash 3.2.
#
# It commits what `/req-done` wrote, pushes, opens the PR from the body file, adds `ci-change` when the diff
# needs it, then hands over to `make ship`, which still asks. Everything it can check it checks before it
# changes anything, and a rerun picks up where the last one stopped.
#
# `baseline-change` and `gate-change` stay the user's own act, and it never sets NW_ALLOW_CONTROL: each is
# a separate decision to accept something (decision 0011). The branch name, the paths and the body are text
# the sandbox wrote: paths are shown through `cat -v`, and none of it reaches a shell.
set -euo pipefail

MAIN_REF=refs/runner/ship-main
HEAD_REF=refs/runner/ship-head
GIT_NAMES=(--no-pager diff --text --no-ext-diff --no-textconv --no-renames --name-only -z)
BRANCH_RE='^req/([A-Z0-9]+-[0-9]+|[0-9]+[a-z]?)(-[a-z0-9-]+)?$'

refuse() {
	printf 'finish: refused: %s\n' "$*" | cat -v >&2
	exit 1
}

say() {
	printf 'finish: %s\n' "$*" | cat -v
}

checkout=${CHECKOUT:-}
reviewed=${SHA:-}
body=${BODY:-}
[[ "$checkout" =~ ^/[A-Za-z0-9._/-]+$ && "$checkout" != *..* ]] || refuse "CHECKOUT must be an absolute path"
[[ "$reviewed" =~ ^[0-9a-f]{40}$ ]] || refuse "SHA must be the 40-character reviewed commit"
[[ "$body" =~ ^docs/reviews/[A-Za-z0-9._-]+\.md$ && "$body" != *..* ]] || refuse "BODY must be a file in docs/reviews"
[[ -d "$checkout" ]] || refuse "no such checkout"

work=$(mktemp -d "${TMPDIR:-/tmp}/finish.XXXXXX")
trap 'rm -rf "$work"' EXIT

in_checkout() {
	git -C "$checkout" "$@"
}

branch=$(in_checkout symbolic-ref -q --short HEAD) || refuse "the checkout is not on a branch"
[[ "$branch" =~ $BRANCH_RE ]] || refuse "not a row branch: $branch"
id=${BASH_REMATCH[1]}
slug=${BASH_REMATCH[2]#-}
title="$id: ${slug//-/ }"
[[ -n "$slug" ]] || title=$id

# Only what `/req-done` writes may be uncommitted.
in_checkout status --porcelain -z --untracked-files=all >"$work/status" || refuse "git status failed"
docs_changed=0
while IFS= read -r -d '' entry; do
	code=${entry:0:2}
	path=${entry:3}
	case "$path" in
	PROGRESS.md | COVERAGE.md)
		[[ "$code" == " M" || "$code" == "M " || "$code" == "MM" ]] || refuse "unexpected state of $path"
		docs_changed=1
		;;
	*) refuse "not committed: ${path//$'\n'/\\n}" ;;
	esac
done <"$work/status"

# What the reviewers read must be what ships, apart from those two files: the rule `ship` applies.
in_checkout merge-base --is-ancestor "$reviewed" HEAD || refuse "the reviewed commit is not on this branch"
in_checkout "${GIT_NAMES[@]}" "$reviewed" HEAD >"$work/later" || refuse "git diff failed"
while IFS= read -r -d '' path; do
	case "$path" in
	PROGRESS.md | COVERAGE.md) ;;
	*) refuse "changed since the reviewed commit: ${path//$'\n'/\\n}" ;;
	esac
done <"$work/later"

# From the reviewed commit, so what is posted is what the reviewers read.
in_checkout show "$reviewed:$body" >"$work/body" 2>/dev/null || refuse "no $body at the reviewed commit"
[[ -s "$work/body" ]] || refuse "$body is empty"

if ((docs_changed)); then
	in_checkout commit -q -m "$id: req-done" -- PROGRESS.md COVERAGE.md || refuse "git commit failed"
	say "committed PROGRESS.md and COVERAGE.md"
fi

# The pre-push hook runs here. Never forced.
in_checkout push origin "HEAD:refs/heads/$branch" 2>&1 | cat -v ||
	refuse "the push failed: read the output above. If the hook refused paths you mean to push, rerun with NW_ALLOW_CONTROL=1 in front"

gh pr list --head "$branch" --base main --state open --json number --jq '.[].number' >"$work/prs" ||
	refuse "gh pr list failed"
open_prs=$(grep -c . "$work/prs" || true)
if ((open_prs > 1)); then refuse "more than one open PR for $branch"; fi
if ((open_prs == 1)); then
	read -r pr <"$work/prs"
	say "PR #$pr is already open"
else
	url=$(gh pr create --base main --head "$branch" --title "$title" --body-file "$work/body") ||
		refuse "gh pr create failed"
	url=${url##*$'\n'}
	[[ "$url" =~ /pull/([0-9]+)$ ]] || refuse "cannot read the PR number from gh"
	pr=${BASH_REMATCH[1]}
	say "opened PR #$pr"
fi
[[ "$pr" =~ ^[0-9]+$ ]] || refuse "cannot read the PR number from gh"

# The branch, not refs/pull: GitHub can move that ref a moment after the push.
git fetch --quiet --no-tags --refmap= origin "+refs/heads/main:$MAIN_REF" "+refs/heads/$branch:$HEAD_REF" ||
	refuse "git fetch failed"
[[ "$(git rev-parse "$HEAD_REF")" == "$(in_checkout rev-parse HEAD)" ]] || refuse "origin does not have the checkout's head"
bases=$(git merge-base --all "$MAIN_REF" "$HEAD_REF") || refuse "no merge base with main"
[[ "$bases" =~ ^[0-9a-f]{40}$ ]] || refuse "no single merge base with main"

/bin/bash tools/ship.sh --labels "$bases" "$HEAD_REF" >"$work/labels" || refuse "cannot read which labels this diff needs"
ci=0
baseline=0
while IFS=$'\t' read -r label path; do
	case "$label" in
	ci-change)
		ci=1
		say "ci-change, because of $path"
		;;
	baseline-change)
		baseline=1
		say "baseline-change needed, because of $path"
		;;
	esac
done <"$work/labels"
if ((ci)); then
	gh pr edit "$pr" --add-label ci-change >/dev/null || refuse "gh pr edit failed"
fi
if ((baseline)); then
	say "baseline-change is yours to add: read the diff, then run gh pr edit $pr --add-label baseline-change, then rerun this command"
fi

exec make --no-print-directory ship PR="$pr" SHA="$reviewed" RUNNER_DIR="$RUNNER_DIR"
