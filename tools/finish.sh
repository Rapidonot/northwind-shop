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
# the sandbox wrote: paths are shown through `cat -v`, and none of it is run.
#
# Git in a checkout is not data: it reads that location's `.git`, config and index, and this runs outside the
# sandbox. So the checkout must be one whose `.git` the sandbox cannot write, a gitlink is refused before
# anything reads the working tree (measured: `git status` runs a nested folder's `core.fsmonitor`), and
# replace refs and grafts are off, because the sandbox can write those.
set -euo pipefail
export LC_ALL=C

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
[[ "$checkout" =~ ^/[A-Za-z0-9._/-]+$ && "$checkout" != *..* && "$checkout" != */ ]] ||
	refuse "CHECKOUT must be an absolute path"
[[ "$reviewed" =~ ^[0-9a-f]{40}$ ]] || refuse "SHA must be the 40-character reviewed commit"
[[ "$body" =~ ^docs/reviews/[A-Za-z0-9._-]+\.md$ && "$body" != *..* ]] || refuse "BODY must be a file in docs/reviews"
[[ -d "$checkout" ]] || refuse "no such checkout"

work=$(mktemp -d "${TMPDIR:-/tmp}/finish.XXXXXX")
trap 'rm -rf "$work"' EXIT

# Exactly what `denyWrite` protects: ~/Projects/northwind-shop, or a linked worktree beside it whose `.git`
# file points into that checkout's own worktrees folder. Physical paths, so a symlink cannot stand in.
projects=$(cd -P "$HOME/Projects" 2>/dev/null && pwd -P) || refuse "not a checkout: no ~/Projects"
real=$(cd -P "$checkout" && pwd -P) || refuse "no such checkout"
main_checkout=$projects/northwind-shop
case "$real" in
"$main_checkout")
	[[ -d "$real/.git" && ! -L "$real/.git" ]] || refuse "not a checkout: $real"
	git_dir=$real/.git
	;;
"$main_checkout"-*)
	[[ "${real#"$main_checkout"-}" =~ ^[A-Za-z0-9._-]+$ ]] || refuse "not a checkout: $real"
	[[ -f "$real/.git" && ! -L "$real/.git" ]] || refuse "not a checkout: $real"
	pointer=$(cat "$real/.git")
	worktrees="gitdir: $main_checkout/.git/worktrees/"
	[[ "$pointer" == "$worktrees"* && "${pointer#"$worktrees"}" =~ ^[A-Za-z0-9._-]+$ ]] ||
		refuse "not a checkout: its .git points elsewhere"
	[[ -d "$main_checkout/.git" && ! -L "$main_checkout/.git" ]] || refuse "not a checkout: no main .git"
	git_dir=$main_checkout/.git/worktrees/${pointer#"$worktrees"}
	[[ -d "$git_dir" && ! -L "$git_dir" ]] || refuse "not a checkout: its admin folder is not a folder"
	# A session can replace this folder whole, past the rules on the files in it (measured, decision 0011).
	[[ ! -L "$git_dir/commondir" && "$(cat "$git_dir/commondir" 2>/dev/null)" == "../.." ]] ||
		refuse "commondir is redirected: do not run git in this checkout until it is put right"
	[[ ! -L "$git_dir/gitdir" && "$(cat "$git_dir/gitdir" 2>/dev/null)" == "$real/.git" ]] ||
		refuse "gitdir points elsewhere: do not run git in this checkout until it is put right"
	;;
*) refuse "not a checkout: $real" ;;
esac
checkout=$real
common_dir=$main_checkout/.git
for redirect in "$common_dir/commondir" "$common_dir/config.worktree" "$git_dir/config.worktree"; do
	[[ ! -e "$redirect" && ! -L "$redirect" ]] ||
		refuse "${redirect##*/} is present: do not run git in this checkout until it is gone"
done

# Told, not discovered: a `commondir` file swapped after the check above changes nothing (measured).
in_checkout() {
	GIT_DIR="$git_dir" GIT_COMMON_DIR="$common_dir" GIT_WORK_TREE="$checkout" \
		GIT_NO_REPLACE_OBJECTS=1 GIT_GRAFT_FILE=/dev/null git -c core.fsmonitor=false -C "$checkout" "$@"
}

# Before anything reads the working tree: a gitlink makes git run a child in that folder, with its config,
# and a `.gitmodules` entry can undo `diff.ignoreSubmodules`, even from the index alone (both measured). So
# neither may be in the index, at HEAD or at the reviewed commit. No tracked path here needs either.
in_checkout ls-files -s -z >"$work/entries" || refuse "git ls-files failed"
in_checkout ls-tree -r -z HEAD >>"$work/entries" || refuse "git ls-tree failed"
in_checkout ls-tree -r -z "$reviewed" >>"$work/entries" || refuse "cannot read the reviewed commit"
tr '\0' '\n' <"$work/entries" >"$work/lines"
{ grep '^160000 ' "$work/lines" || true; } | cut -f2 >"$work/gitlinks"
if [[ -s "$work/gitlinks" ]]; then
	cat -v "$work/gitlinks" >&2
	refuse "a gitlink is tracked: do not run git status in this checkout until it is gone"
fi
# Through a file: `| grep -q` closes the pipe on a match, and under pipefail that reads as no match (measured).
cut -f2 "$work/lines" | { grep -E '(^|/)\.gitmodules$' || true; } >"$work/modules"
if [[ -s "$work/modules" ]]; then
	refuse "a .gitmodules file is tracked: do not run git status in this checkout until it is gone"
fi
[[ ! -e "$checkout/.gitmodules" ]] || refuse "a .gitmodules file is in the checkout"

branch=$(in_checkout symbolic-ref -q --short HEAD) || refuse "the checkout is not on a branch"
[[ "$branch" =~ $BRANCH_RE ]] || refuse "not a row branch: $branch"
id=${BASH_REMATCH[1]}
slug=${BASH_REMATCH[2]#-}
title="$id: ${slug//-/ }"
[[ -n "$slug" ]] || title=$id

# Only what `/req-done` writes may be uncommitted.
in_checkout status --porcelain -z --untracked-files=all --ignore-submodules=all >"$work/status" ||
	refuse "git status failed"
docs_changed=0
while IFS= read -r -d '' entry; do
	code=${entry:0:2}
	path=${entry:3}
	case "$path" in
	PROGRESS.md | COVERAGE.md)
		[[ "$code" == " M" || "$code" == "M " ]] || refuse "unexpected state of $path"
		docs_changed=1
		;;
	*) refuse "not committed: ${path//$'\n'/\\n}" ;;
	esac
done <"$work/status"

# What the reviewers read must be what ships, apart from those two files: the rule `ship` applies.
# Resolved once: what is checked is what is pushed.
check_reviewed() {
	local path
	head=$(in_checkout rev-parse HEAD) || refuse "git rev-parse failed"
	in_checkout merge-base --is-ancestor "$reviewed" "$head" || refuse "the reviewed commit is not on this branch"
	in_checkout "${GIT_NAMES[@]}" "$reviewed" "$head" >"$work/later" || refuse "git diff failed"
	while IFS= read -r -d '' path; do
		case "$path" in
		PROGRESS.md | COVERAGE.md) ;;
		*) refuse "changed since the reviewed commit: ${path//$'\n'/\\n}" ;;
		esac
	done <"$work/later"
}
check_reviewed

# From the reviewed commit, so what is posted is what the reviewers read.
in_checkout show "$reviewed:$body" >"$work/body" 2>/dev/null || refuse "no $body at the reviewed commit"
[[ -s "$work/body" ]] || refuse "$body is empty"

if ((docs_changed)); then
	# --allow-empty: with nothing to commit, `git commit` runs a status of its own that descends into gitlinks.
	in_checkout commit -q --allow-empty -m "$id: req-done" -- PROGRESS.md COVERAGE.md || refuse "git commit failed"
	say "committed PROGRESS.md and COVERAGE.md"
	check_reviewed
fi

# The pre-push hook runs here. Never forced, and the commit that was checked, not whatever HEAD is now.
in_checkout push origin "$head:refs/heads/$branch" 2>&1 | cat -v ||
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
[[ "$(git rev-parse "$HEAD_REF")" == "$head" ]] || refuse "origin does not have the checkout's head"
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
